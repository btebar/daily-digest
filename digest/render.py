"""Render the curated selections into an HTML email."""

from __future__ import annotations

import html
from collections import OrderedDict
from datetime import datetime


def _esc(s: str) -> str:
    return html.escape(s or "")


def _featured_block(featured: list[dict]) -> str:
    if not featured:
        return ""
    cards = []
    for i, sel in enumerate(featured, 1):
        it = sel["item"]
        cards.append(
            f"""
            <div style="margin:0 0 22px 0;padding:16px 18px;background:#faf5ff;
                        border-radius:10px;border:1px solid #f0e6ff;">
              <div style="font-size:12px;font-weight:700;color:#9333ea;">#{i}</div>
              <a href="{_esc(it.url)}"
                 style="font-size:17px;font-weight:700;color:#1a1a1a;
                        text-decoration:none;line-height:1.35;">{_esc(it.title)}</a>
              <div style="font-size:12px;color:#6b7280;margin:3px 0 8px;">
                 {_esc(it.source)}</div>
              <div style="font-size:14.5px;color:#374151;line-height:1.6;">
                 {_esc(sel["summary"])}</div>
            </div>"""
        )
    return (
        '<h2 style="font-size:14px;text-transform:uppercase;letter-spacing:.05em;'
        'color:#111;margin:22px 0 14px;">Top picks, explained</h2>' + "".join(cards)
    )


def _more_block(more: list[dict]) -> str:
    if not more:
        return ""
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for sel in more:
        groups.setdefault(sel["category"], []).append(sel)

    sections = []
    for category, sels in groups.items():
        rows = []
        for sel in sels:
            it = sel["item"]
            rows.append(
                f"""
                <div style="margin:0 0 14px 0;">
                  <a href="{_esc(it.url)}"
                     style="font-size:15px;font-weight:600;color:#1a1a1a;
                            text-decoration:none;line-height:1.35;">{_esc(it.title)}</a>
                  <div style="font-size:12px;color:#6b7280;margin:2px 0 4px;">
                     {_esc(it.source)}</div>
                  <div style="font-size:13.5px;color:#374151;line-height:1.5;">
                     {_esc(sel["one_liner"])}</div>
                </div>"""
            )
        sections.append(
            f"""
            <h3 style="font-size:13px;text-transform:uppercase;letter-spacing:.05em;
                       color:#9333ea;border-bottom:1px solid #eee;padding-bottom:5px;
                       margin:24px 0 14px;">{_esc(category)}</h3>{"".join(rows)}"""
        )
    return (
        '<h2 style="font-size:14px;text-transform:uppercase;letter-spacing:.05em;'
        'color:#111;margin:34px 0 6px;">Also worth a look</h2>' + "".join(sections)
    )


def render_html(featured: list[dict], more: list[dict], date: datetime) -> str:
    total = len(featured) + len(more)
    body = _featured_block(featured) + _more_block(more)
    if total == 0:
        body = (
            '<p style="color:#6b7280;">Nothing notable enough to surface today. Quiet is good.</p>'
        )
    return f"""\
<div style="max-width:640px;margin:0 auto;padding:24px 20px;
            font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <div style="font-size:22px;font-weight:700;color:#111;">Your Daily Digest</div>
  <div style="font-size:13px;color:#6b7280;margin:2px 0 8px;">
    {date.strftime("%A, %d %B %Y")} · {total} items</div>
  {body}
  <div style="margin-top:36px;padding-top:14px;border-top:1px solid #eee;
              font-size:12px;color:#9ca3af;">
    Curated from arXiv + your feeds. Edit topics & sources in config.yaml.
  </div>
</div>"""


def render_text(featured: list[dict], more: list[dict], date: datetime) -> str:
    lines = [f"Your Daily Digest — {date.strftime('%A, %d %B %Y')}", ""]
    if featured:
        lines += ["TOP PICKS, EXPLAINED", ""]
    for i, sel in enumerate(featured, 1):
        it = sel["item"]
        lines += [f"#{i} {it.title}", f"  {sel['summary']}", f"  {it.source} — {it.url}", ""]
    if more:
        lines += ["ALSO WORTH A LOOK", ""]
    for sel in more:
        it = sel["item"]
        lines += [
            f"[{sel['category']}] {it.title}",
            f"  {sel['one_liner']}",
            f"  {it.source} — {it.url}",
            "",
        ]
    if not featured and not more:
        lines.append("Nothing notable enough to surface today.")
    return "\n".join(lines)
