"""Collect candidate items from arXiv and RSS/Atom feeds."""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import feedparser
import requests

_HEADERS = {"User-Agent": "daily-digest/1.0 (+https://github.com)"}


def _fetch_feed(url: str, retries: int = 0):
    """Fetch a URL with requests (bundled CA certs) and hand bytes to feedparser.

    Using requests avoids macOS/urllib SSL 'CERTIFICATE_VERIFY_FAILED' issues
    that bite local runs; on CI it behaves identically. `retries` adds a short
    backoff on HTTP 429 (Reddit rate-limits aggressively)."""
    for attempt in range(retries + 1):
        resp = requests.get(url, headers=_HEADERS, timeout=30)
        if resp.status_code == 429 and attempt < retries:
            time.sleep(3 * (attempt + 1))
            continue
        resp.raise_for_status()
        return feedparser.parse(resp.content)


@dataclass
class Item:
    title: str
    url: str
    source: str
    kind: str  # "paper" | "article"
    published: datetime | None = None
    summary: str = ""

    def dedup_key(self) -> str:
        return self.url.split("?")[0].rstrip("/").lower()


def _struct_to_dt(parsed) -> datetime | None:
    if not parsed:
        return None
    return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)


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


def collect_rss(feeds: list[dict], cutoff: datetime) -> list[Item]:
    items: list[Item] = []
    for f in feeds:
        name, url = f.get("name", "Feed"), f.get("url")
        if not url:
            continue
        try:
            parsed = _fetch_feed(url)
        except Exception as exc:  # a single broken feed shouldn't kill the run
            print(f"  ! failed to parse {name}: {exc}")
            continue
        for e in parsed.entries:
            published = _struct_to_dt(
                getattr(e, "published_parsed", None)
                or getattr(e, "updated_parsed", None)
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
    return [i for i in items if _recent(i, cutoff)]


def collect_reddit(cfg: dict, cutoff: datetime) -> list[Item]:
    subs = cfg.get("subreddits", [])
    period = cfg.get("top_period", "day")
    per_sub = cfg.get("per_sub", 12)
    items: list[Item] = []
    for i, sub in enumerate(subs):
        if i:  # be polite between requests — Reddit rate-limits hard
            time.sleep(2)
        url = f"https://www.reddit.com/r/{sub}/top/.rss?t={period}&limit={per_sub}"
        try:
            parsed = _fetch_feed(url, retries=2)
        except Exception as exc:  # 429/403/404 on one sub shouldn't kill the run
            print(f"  ! failed to fetch r/{sub}: {exc}")
            continue
        for e in parsed.entries:
            published = _struct_to_dt(getattr(e, "published_parsed", None))
            summary = getattr(e, "summary", "") or getattr(e, "content", "")
            if isinstance(summary, list):  # feedparser sometimes wraps content
                summary = summary[0].get("value", "") if summary else ""
            items.append(
                Item(
                    title=" ".join(getattr(e, "title", "").split()),
                    url=getattr(e, "link", ""),
                    source=f"r/{sub}",
                    kind="discussion",
                    published=published,
                    summary=" ".join(str(summary).split())[:400],
                )
            )
    return [i for i in items if _recent(i, cutoff)]


def collect_all(config: dict) -> list[Item]:
    lookback = config["curation"]["lookback_hours"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)
    sources = config.get("sources", {})

    items: list[Item] = []
    arxiv = sources.get("arxiv")
    if arxiv:
        found = collect_arxiv(
            arxiv.get("categories", []), arxiv.get("max_results", 60), cutoff
        )
        print(f"  arXiv: {len(found)} recent papers")
        items += found

    rss = sources.get("rss", [])
    if rss:
        found = collect_rss(rss, cutoff)
        print(f"  RSS:   {len(found)} recent articles")
        items += found

    reddit = sources.get("reddit")
    if reddit and reddit.get("subreddits"):
        found = collect_reddit(reddit, cutoff)
        print(f"  Reddit: {len(found)} recent posts")
        items += found

    # Dedupe by normalised URL, keep first seen.
    seen: set[str] = set()
    unique: list[Item] = []
    for i in items:
        if not i.url or i.dedup_key() in seen:
            continue
        seen.add(i.dedup_key())
        unique.append(i)
    print(f"  Total unique candidates: {len(unique)}")
    return unique
