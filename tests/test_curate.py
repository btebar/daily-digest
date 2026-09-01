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
    def __init__(self, text, script=None):
        self._text = text
        # Optional list of exceptions to raise before succeeding, for retry tests.
        self._script = list(script or [])
        self.calls: list[str] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs.get("model"))
        if self._script:
            raise self._script.pop(0)
        return type("R", (), {"text": self._text})()


class _FakeClient:
    def __init__(self, text, script=None):
        self.models = _FakeModels(text, script)


def _patch_client(monkeypatch, text, script=None):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = _FakeClient(text, script)
    monkeypatch.setattr(curate_mod.genai, "Client", lambda *a, **k: client)
    # Don't actually wait out the backoff in tests.
    monkeypatch.setattr(curate_mod.time, "sleep", lambda _s: None)
    return client


class _ApiError(curate_mod.genai_errors.APIError):
    """Stand-in for a google.genai APIError with a given HTTP status."""

    def __init__(self, code: int):
        self.code = code
        Exception.__init__(self, f"{code} error")


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


_OK = json.dumps({"featured": [{"id": 0, "summary": "S0"}], "more": []})


class TestTransientRetries:
    """A 503 ('model experiencing high demand') is the failure that was killing
    the scheduled run — it must be retried, not fatal."""

    def test_retries_a_503_and_then_succeeds(self, monkeypatch):
        client = _patch_client(monkeypatch, _OK, script=[_ApiError(503), _ApiError(503)])
        out = curate(_items(2), CONFIG)
        assert out["featured"][0]["item"].url == "https://x/0"
        assert client.models.calls == ["fake", "fake", "fake"]

    def test_falls_back_to_the_second_model(self, monkeypatch):
        config = {**CONFIG, "curation": {**CONFIG["curation"], "fallback_model": "backup"}}
        # Exhaust every attempt on the primary (1 + len(_RETRY_WAITS)).
        script = [_ApiError(503)] * (len(curate_mod._RETRY_WAITS) + 1)
        client = _patch_client(monkeypatch, _OK, script=script)
        out = curate(_items(2), config)
        assert out["featured"][0]["item"].url == "https://x/0"
        assert client.models.calls[-1] == "backup"

    def test_gives_up_after_exhausting_every_model(self, monkeypatch):
        config = {**CONFIG, "curation": {**CONFIG["curation"], "fallback_model": "backup"}}
        attempts = len(curate_mod._RETRY_WAITS) + 1
        script = [_ApiError(503)] * (attempts * 2)
        _patch_client(monkeypatch, _OK, script=script)
        with pytest.raises(curate_mod.genai_errors.APIError):
            curate(_items(2), config)

    def test_permanent_error_is_not_retried(self, monkeypatch):
        # A 400 (bad request) won't fix itself — fail fast rather than
        # sitting through the backoff.
        client = _patch_client(monkeypatch, _OK, script=[_ApiError(400)])
        with pytest.raises(curate_mod.genai_errors.APIError):
            curate(_items(2), CONFIG)
        assert client.models.calls == ["fake"]

    def test_fallback_equal_to_primary_is_ignored(self, monkeypatch):
        config = {**CONFIG, "curation": {**CONFIG["curation"], "fallback_model": "fake"}}
        client = _patch_client(monkeypatch, _OK)
        curate(_items(2), config)
        assert client.models.calls == ["fake"]
