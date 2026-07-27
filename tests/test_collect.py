"""Tests for candidate collection: dedup, recency, fetch retries, feed parsing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from digest import collect
from digest.collect import Item, _collect_one_rss, _fetch_feed, _recent, collect_all


def _item(url: str, published: datetime | None = None) -> Item:
    return Item(title="t", url=url, source="s", kind="article", published=published)


class TestDedupKey:
    def test_strips_query_and_trailing_slash_and_lowercases(self):
        assert _item("https://Example.com/Post/?utm=1").dedup_key() == ("https://example.com/post")

    def test_query_variants_collapse_to_same_key(self):
        a = _item("https://x.com/a?ref=twitter")
        b = _item("https://x.com/a/")
        assert a.dedup_key() == b.dedup_key()


class TestRecent:
    def test_keeps_undated_items(self):
        cutoff = datetime.now(UTC)
        assert _recent(_item("u", published=None), cutoff) is True

    def test_keeps_items_at_or_after_cutoff(self):
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        assert _recent(_item("u", published=datetime.now(UTC)), cutoff) is True

    def test_drops_items_before_cutoff(self):
        cutoff = datetime.now(UTC)
        old = datetime.now(UTC) - timedelta(days=2)
        assert _recent(_item("u", published=old), cutoff) is False


class TestCollectAllDedup:
    def test_dedupes_across_sources_and_drops_empty_urls(self, monkeypatch):
        dupe = [
            _item("https://x.com/a"),
            _item("https://x.com/a/"),  # same normalised key
            _item(""),  # no url -> dropped
            _item("https://x.com/b"),
        ]
        monkeypatch.setattr(collect, "collect_arxiv", lambda *a, **k: dupe)
        monkeypatch.setattr(collect, "collect_rss", lambda *a, **k: [])
        config = {
            "curation": {"lookback_hours": 72},
            "sources": {"arxiv": {"categories": ["cs.AI"]}},
        }
        out = collect_all(config)
        assert [i.url for i in out] == ["https://x.com/a", "https://x.com/b"]


class _FakeResp:
    def __init__(self, status_code=200, content=b"", json_data=None):
        self.status_code = status_code
        self.content = content
        self._json = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise collect.requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json


class TestFetchFeed:
    def test_retries_on_429_then_succeeds(self, monkeypatch):
        calls = {"n": 0}

        def fake_get(url, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResp(status_code=429)
            return _FakeResp(status_code=200, content=b"<rss></rss>")

        monkeypatch.setattr(collect.time, "sleep", lambda *_: None)
        monkeypatch.setattr(collect.requests, "get", fake_get)
        _fetch_feed("https://x.com/feed", retries=2)
        assert calls["n"] == 2

    def test_raises_after_exhausting_retries(self, monkeypatch):
        def always_429(url, **kwargs):
            return _FakeResp(status_code=429)

        monkeypatch.setattr(collect.time, "sleep", lambda *_: None)
        monkeypatch.setattr(collect.requests, "get", always_429)
        with pytest.raises(collect.requests.HTTPError):
            _fetch_feed("https://x.com/feed", retries=1)


class TestCollectOneRss:
    def test_broken_feed_is_swallowed_returns_empty(self, monkeypatch):
        def boom(url, retries=2):
            raise RuntimeError("network down")

        monkeypatch.setattr(collect, "_fetch_feed", boom)
        assert _collect_one_rss({"name": "Broken", "url": "https://x/feed"}) == []

    def test_missing_url_returns_empty(self):
        assert _collect_one_rss({"name": "NoUrl"}) == []
