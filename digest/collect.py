"""Collect candidate items from arXiv and RSS/Atom feeds."""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import feedparser
import requests

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "daily-digest/1.0 (+https://github.com)"}

# Max feeds/subreddits fetched at once. Network-bound work, so threads are fine.
_MAX_WORKERS = 8


def _fetch_feed(url: str, retries: int = 2) -> feedparser.FeedParserDict:
    """Fetch a URL with requests (bundled CA certs) and hand bytes to feedparser.

    Using requests avoids macOS/urllib SSL 'CERTIFICATE_VERIFY_FAILED' issues
    that bite local runs; on CI it behaves identically. `retries` adds a short
    backoff on HTTP 429 (Reddit rate-limits aggressively)."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise
        if resp.status_code == 429 and attempt < retries:
            time.sleep(3 * (attempt + 1))
            continue
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    # Only reached if every attempt hit a retryable error above.
    raise last_exc if last_exc else RuntimeError(f"failed to fetch {url}")


@dataclass
class Item:
    title: str
    url: str
    source: str
    kind: str  # "paper" | "article" | "discussion"
    published: datetime | None = None
    summary: str = ""

    def dedup_key(self) -> str:
        return self.url.split("?")[0].rstrip("/").lower()


def _struct_to_dt(parsed) -> datetime | None:
    if not parsed:
        return None
    return datetime.fromtimestamp(time.mktime(parsed), tz=UTC)


def _recent(item: Item, cutoff: datetime) -> bool:
    # Keep items with no date (feeds sometimes omit it) rather than drop them.
    return item.published is None or item.published >= cutoff


def collect_arxiv(categories: list[str], max_results: int, cutoff: datetime) -> list[Item]:
    if not categories:
        return []
    query = " OR ".join(f"cat:{c}" for c in categories)
    params = urllib.parse.urlencode(
        {
            "search_query": query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": max_results,
        }
    )
    feed = _fetch_feed(f"https://export.arxiv.org/api/query?{params}")
    items: list[Item] = []
    for e in feed.entries:
        published = _struct_to_dt(getattr(e, "published_parsed", None))
        items.append(
            Item(
                title=" ".join(getattr(e, "title", "").split()),
                url=getattr(e, "link", ""),
                source="arXiv",
                kind="paper",
                published=published,
                summary=" ".join(getattr(e, "summary", "").split()),
            )
        )
    return [i for i in items if _recent(i, cutoff)]


def _collect_one_rss(f: dict) -> list[Item]:
    name, url = f.get("name", "Feed"), f.get("url")
    if not url:
        return []
    try:
        parsed = _fetch_feed(url)
    except Exception as exc:  # a single broken feed shouldn't kill the run
        log.warning("failed to parse %s: %s", name, exc)
        return []
    items: list[Item] = []
    for e in parsed.entries:
        published = _struct_to_dt(
            getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        )
        summary = getattr(e, "summary", "") or getattr(e, "description", "")
        items.append(
            Item(
                title=" ".join(getattr(e, "title", "").split()),
                url=getattr(e, "link", ""),
                source=name,
                kind="article",
                published=published,
                summary=" ".join(summary.split())[:600],
            )
        )
    return items


def collect_rss(feeds: list[dict], cutoff: datetime) -> list[Item]:
    if not feeds:
        return []
    items: list[Item] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        for found in pool.map(_collect_one_rss, feeds):
            items += found
    return [i for i in items if _recent(i, cutoff)]


def _reddit_token(client_id: str, client_secret: str) -> str:
    """App-only OAuth (client_credentials): read-only access, 100 req/min."""
    resp = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers=_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def collect_reddit(cfg: dict, cutoff: datetime) -> list[Item]:
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not (client_id and client_secret):
        log.info("Reddit: skipped (REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set)")
        return []

    try:
        token = _reddit_token(client_id, client_secret)
    except Exception as exc:
        log.warning("Reddit auth failed: %s", exc)
        return []
    headers = {**_HEADERS, "Authorization": f"bearer {token}"}

    subs = cfg.get("subreddits", [])
    period = cfg.get("top_period", "day")
    per_sub = cfg.get("per_sub", 12)

    def _fetch_sub(sub: str) -> list[Item]:
        url = f"https://oauth.reddit.com/r/{sub}/top?t={period}&limit={per_sub}"
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            children = resp.json().get("data", {}).get("children", [])
        except Exception as exc:  # a bad/private sub shouldn't kill the run
            log.warning("failed to fetch r/%s: %s", sub, exc)
            return []
        out: list[Item] = []
        for c in children:
            p = c.get("data", {})
            created = p.get("created_utc")
            published = datetime.fromtimestamp(created, tz=UTC) if created else None
            out.append(
                Item(
                    title=" ".join((p.get("title") or "").split()),
                    # Link to the Reddit discussion, not the external URL.
                    url="https://www.reddit.com" + p.get("permalink", ""),
                    source=f"r/{sub}",
                    kind="discussion",
                    published=published,
                    summary=" ".join((p.get("selftext") or "").split())[:400],
                )
            )
        return out

    items: list[Item] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        for found in pool.map(_fetch_sub, subs):
            items += found
    return [i for i in items if _recent(i, cutoff)]


def collect_all(config: dict) -> list[Item]:
    lookback = config["curation"]["lookback_hours"]
    cutoff = datetime.now(UTC) - timedelta(hours=lookback)
    sources = config.get("sources", {})

    items: list[Item] = []
    arxiv = sources.get("arxiv")
    if arxiv:
        found = collect_arxiv(arxiv.get("categories", []), arxiv.get("max_results", 60), cutoff)
        log.info("arXiv: %d recent papers", len(found))
        items += found

    rss = sources.get("rss", [])
    if rss:
        found = collect_rss(rss, cutoff)
        log.info("RSS: %d recent articles", len(found))
        items += found

    reddit = sources.get("reddit")
    if reddit and reddit.get("subreddits"):
        found = collect_reddit(reddit, cutoff)
        if found:
            log.info("Reddit: %d recent posts", len(found))
        items += found

    # Dedupe by normalised URL, keep first seen.
    seen: set[str] = set()
    unique: list[Item] = []
    for i in items:
        if not i.url or i.dedup_key() in seen:
            continue
        seen.add(i.dedup_key())
        unique.append(i)
    log.info("Total unique candidates: %d", len(unique))
    return unique
