"""Tests for curation: the model returns indices, we map them safely to Items."""

from __future__ import annotations

import json

import pytest

from digest import curate as curate_mod
from digest.collect import Item
from digest.curate import curate


def _items(n: int) -> list[Item]:
    return [Item(title=f"t{i}", url=f"https://x/{i}", source="s", kind="article") for i in range(n)]


CONFIG = {
    "curation": {"model": "fake", "max_items": 12, "notability": "x"},
    "topics": ["a", "b"],
}


class _FakeModels:
    def __init__(self, text):
        self._text = text

    def generate_content(self, **kwargs):
        return type("R", (), {"text": self._text})()


class _FakeClient:
    def __init__(self, text):
        self.models = _FakeModels(text)


def _patch_client(monkeypatch, text):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(curate_mod.genai, "Client", lambda *a, **k: _FakeClient(text))


def test_empty_candidates_short_circuits():
    assert curate([], CONFIG) == {"featured": [], "more": []}


def test_maps_indices_to_items(monkeypatch):
    payload = json.dumps(
        {
            "featured": [{"id": 0, "summary": "S0"}],
            "more": [{"id": 1, "category": "Cat", "one_liner": "L1"}],
        }
    )
    _patch_client(monkeypatch, payload)
    out = curate(_items(3), CONFIG)
    assert out["featured"][0]["item"].url == "https://x/0"
    assert out["featured"][0]["summary"] == "S0"
    assert out["more"][0]["item"].url == "https://x/1"
    assert out["more"][0]["category"] == "Cat"


def test_drops_out_of_range_and_duplicate_ids(monkeypatch):
    payload = json.dumps(
        {
            "featured": [{"id": 0, "summary": "ok"}, {"id": 99, "summary": "bad"}],
            "more": [
                {"id": 0, "category": "dup", "one_liner": "x"},  # already featured
                {"id": -1, "category": "neg", "one_liner": "x"},
            ],
        }
    )
    _patch_client(monkeypatch, payload)
    out = curate(_items(3), CONFIG)
    assert [f["item"].url for f in out["featured"]] == ["https://x/0"]
    assert out["more"] == []


def test_empty_model_response_raises(monkeypatch):
    _patch_client(monkeypatch, None)
    with pytest.raises(RuntimeError, match="empty response"):
        curate(_items(2), CONFIG)


def test_invalid_json_raises(monkeypatch):
    _patch_client(monkeypatch, "not json{{")
    with pytest.raises(RuntimeError, match="invalid JSON"):
        curate(_items(2), CONFIG)


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        curate(_items(2), CONFIG)
