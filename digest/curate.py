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

from google import genai
from google.genai import types
from pydantic import BaseModel

from .collect import Item

log = logging.getLogger(__name__)


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
        "You are a sharp research analyst curating a personalised daily digest "
        "for a technical reader who wants to actually learn from it. You are given "
        "the reader's interests and a list of candidate items (papers, articles, "
        "releases, discussions) from the last few days. Your job is to surface the "
        "best of what's available relative to the reader's interests. Always fill "
        "the digest with the strongest, most relevant items you can find — even on "
        "a quiet day there is almost always something worth the reader's attention. "
        "Return an empty selection ONLY if there are genuinely no items even "
        f"loosely relevant to the interests below. {cur['notability']}"
    )

    user = (
        f"READER'S INTERESTS:\n{topics}\n\n"
        f"Select up to {max_items} items total, split into two tiers. Aim to fill "
        "the digest — pick the best available even if the day is quiet.\n\n"
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
        "Use each item's 'id' exactly as given. Rank most notable first. Prefer to "
        "return a full digest; only return fewer items if the candidate pool is "
        "small or genuinely off-topic. When quality and relevance are comparable, "
        "favour a mix of sources (papers, articles, and discussions) rather than "
        "selecting only papers.\n\n"
        f"CANDIDATES:\n{catalogue}"
    )

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
    client = genai.Client(api_key=api_key)

    resp = client.models.generate_content(
        model=cur["model"],
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=_Curation,
        ),
    )
    # A blocked or empty response yields resp.text == None; don't let json.loads
    # throw an opaque "expected value" error — fail with a clear message instead.
    if not resp.text:
        raise RuntimeError("Gemini returned an empty response (blocked or truncated)")
    try:
        data = json.loads(resp.text)
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
