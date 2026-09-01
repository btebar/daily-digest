"""Use Gemini to select and summarise the most notable items.

Returns two things:
  - featured: the top ~5 items, each with a clear teaching paragraph
  - more:     the remaining notable items, grouped by category with a one-liner

The model returns indices into the candidate list (not URLs), so it cannot
hallucinate links — we map indices back to the real Items ourselves.
"""

from __future__ import annotations

import json
import logging
import os
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from .collect import Item

log = logging.getLogger(__name__)

# Gemini returns 503 UNAVAILABLE ("model is currently experiencing high demand")
# fairly often at busy times of day, and 429 when the free-tier quota is hit.
# Both are transient, so wait and try again rather than failing the whole run.
# The client library's own retries give up in a couple of seconds; these waits
# are long enough to outlast a real demand spike.
_RETRY_WAITS = (20, 60, 150)  # seconds before attempts 2, 3, 4
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


# Structured-output schema (Gemini validates against these Pydantic models).
class _Featured(BaseModel):
    id: int
    summary: str


class _More(BaseModel):
    id: int
    category: str
    one_liner: str


class _Curation(BaseModel):
    featured: list[_Featured]
    more: list[_More]


def _valid(idx, items) -> bool:
    return isinstance(idx, int) and 0 <= idx < len(items)


def _is_transient(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code in _TRANSIENT_STATUS


def _generate(client, model: str, contents: str, gen_config) -> str:
    """Call Gemini, retrying transient errors, then falling back to `fallback`.

    Raises the last error if every attempt on every model fails."""
    resp = client.models.generate_content(model=model, contents=contents, config=gen_config)
    # A blocked or empty response yields resp.text == None; don't let json.loads
    # throw an opaque "expected value" error — fail with a clear message instead.
    if not resp.text:
        raise RuntimeError("Gemini returned an empty response (blocked or truncated)")
    return resp.text


def _generate_with_retry(client, models: list[str], contents: str, gen_config) -> str:
    """Try each model in turn, retrying transient failures with a backoff."""
    if not models:
        raise RuntimeError("no curation model configured")
    last_exc: Exception | None = None
    for m, model in enumerate(models):
        is_last_model = m == len(models) - 1
        for attempt, wait in enumerate((*_RETRY_WAITS, None), start=1):
            try:
                return _generate(client, model, contents, gen_config)
            except genai_errors.APIError as exc:
                last_exc = exc
                if not _is_transient(exc):
                    log.warning("%s failed permanently: %s", model, exc)
                    break
                if wait is None:
                    log.warning("%s still failing after %d attempts", model, attempt)
                    break
                log.warning(
                    "%s unavailable (%s) — retrying in %ds (attempt %d of %d)",
                    model,
                    getattr(exc, "code", "?"),
                    wait,
                    attempt + 1,
                    len(_RETRY_WAITS) + 1,
                )
                time.sleep(wait)
        if not is_last_model:
            log.warning("Falling back to %s", models[m + 1])
    raise last_exc if last_exc else RuntimeError("curation failed with no recorded error")


def curate(items: list[Item], config: dict) -> dict:
    """Return {"featured": [{item, summary}], "more": [{item, category, one_liner}]}."""
    if not items:
        return {"featured": [], "more": []}

    cur = config["curation"]
    topics = "\n".join(f"- {t}" for t in config["topics"])
    max_items = cur["max_items"]
    n_featured = min(5, max_items)

    catalogue = "\n".join(
        f"[{idx}] ({it.kind}, {it.source}) {it.title}\n     {it.summary[:500]}"
        for idx, it in enumerate(items)
    )

    system = (
        "You are a sharp research analyst curating a personalised weekly digest "
        "for a technical reader who wants to actually learn from it. You are given "
        "the reader's interests and a list of candidate items (papers, articles, "
        "releases, discussions) from the past week. Your job is to surface the "
        "genuine highlights of the week relative to the reader's interests. The "
        "candidate pool is large and covers seven days, so be selective: this is a "
        "weekly roundup, and the reader would rather have a few excellent items "
        "than a full list padded with mediocre ones. "
        "Return an empty selection ONLY if there are genuinely no items even "
        f"loosely relevant to the interests below. {cur['notability']}"
    )

    user = (
        f"READER'S INTERESTS:\n{topics}\n\n"
        f"Select up to {max_items} items total, split into two tiers. This is a "
        "whole week of candidates, so choose the standouts rather than filling "
        "the quota.\n\n"
        f"1. featured — the {n_featured} most notable/important items (HIGH bar: "
        "genuinely novel or significant for this reader). For each, write a clear, "
        "self-contained paragraph (roughly 4-6 sentences) that a curious non-expert "
        "can learn from: what the work is, the key idea or finding in plain "
        "language, and why it matters for this reader's interests. Avoid jargon; "
        "explain any term you must use.\n\n"
        "2. more — additional items that are relevant and worth a glance (LOWER bar "
        "than featured: they need not be groundbreaking, just useful or interesting "
        "to this reader). For each, give a short natural category heading (e.g. "
        "'Agents & memory', 'Health & fitness AI', 'Evals & reward hacking') and a "
        "single-sentence one_liner. Do NOT repeat any id that appears in featured.\n\n"
        "Use each item's 'id' exactly as given. Rank most notable first. Return "
        "fewer items if the week genuinely didn't produce enough that clears the "
        "bar. When quality and relevance are comparable, "
        "favour a mix of sources (papers, articles, and discussions) rather than "
        "selecting only papers.\n\n"
        f"CANDIDATES:\n{catalogue}"
    )

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
    client = genai.Client(api_key=api_key)

    # Primary model, then any configured fallback — a 503 is per-model, so a
    # different model is often available when the first one is swamped.
    models = [cur["model"]]
    fallback = cur.get("fallback_model")
    if fallback and fallback != cur["model"]:
        models.append(fallback)

    text = _generate_with_retry(
        client,
        models,
        user,
        types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=_Curation,
        ),
    )
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"Gemini returned invalid JSON: {exc}") from exc

    featured: list[dict] = []
    used: set[int] = set()
    for sel in data.get("featured", [])[:n_featured]:
        idx = sel.get("id")
        if not _valid(idx, items) or idx in used:
            continue
        used.add(idx)
        featured.append({"item": items[idx], "summary": sel.get("summary", "")})

    more: list[dict] = []
    for sel in data.get("more", []):
        idx = sel.get("id")
        if not _valid(idx, items) or idx in used:
            continue
        used.add(idx)
        more.append(
            {
                "item": items[idx],
                "category": sel.get("category", "Notable"),
                "one_liner": sel.get("one_liner", ""),
            }
        )

    return {"featured": featured, "more": more[: max_items - len(featured)]}
