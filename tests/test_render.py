"""Tests for HTML/text rendering, especially escaping of untrusted feed content."""

from __future__ import annotations

from datetime import datetime

from digest.collect import Item
from digest.render import render_html, render_text

DATE = datetime(2026, 7, 27, 8, 0)


def _featured(title="Title", summary="Summary", url="https://x/a"):
    return [{"item": Item(title=title, url=url, source="src", kind="paper"), "summary": summary}]


def _more(title="M", one_liner="one", category="Cat", url="https://x/b"):
    return [
        {
            "item": Item(title=title, url=url, source="src", kind="article"),
            "category": category,
            "one_liner": one_liner,
        }
    ]


class TestRenderHtml:
    def test_escapes_html_in_title_and_summary(self):
        html = render_html(
            _featured(title="<script>alert(1)</script>", summary="a & b < c"),
            [],
            DATE,
        )
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
        assert "a &amp; b &lt; c" in html

    def test_item_count_in_header(self):
        html = render_html(_featured(), _more(), DATE)
        assert "2 items" in html

    def test_empty_selection_shows_quiet_message(self):
        html = render_html([], [], DATE)
        assert "Nothing notable" in html

    def test_groups_more_items_by_category(self):
        more = [
            {
                "item": Item(title="A", url="https://x/1", source="s", kind="article"),
                "category": "Agents",
                "one_liner": "l1",
            },
            {
                "item": Item(title="B", url="https://x/2", source="s", kind="article"),
                "category": "Agents",
                "one_liner": "l2",
            },
        ]
        html = render_html([], more, DATE)
        # One heading for the shared category, both titles present.
        assert html.count(">Agents<") == 1
        assert ">A<" in html and ">B<" in html


class TestRenderText:
    def test_includes_titles_urls_and_headers(self):
        text = render_text(_featured(title="Paper X"), _more(title="Note Y"), DATE)
        assert "TOP PICKS, EXPLAINED" in text
        assert "Paper X" in text
        assert "https://x/a" in text
        assert "ALSO WORTH A LOOK" in text
        assert "Note Y" in text

    def test_empty_selection_message(self):
        assert "Nothing notable" in render_text([], [], DATE)
