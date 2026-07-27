"""Tests for the send gate and the seen-URL retention cache."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from digest import main

CONFIG = {"send_time": {"hour": 7, "timezone": "Europe/London"}}


def _at(hour: int) -> datetime:
    return datetime(2026, 7, 27, hour, 0, tzinfo=ZoneInfo("Europe/London"))


class TestSendGate:
    def test_blocks_before_target_hour(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "STATE_PATH", tmp_path / "last_sent")
        ok, reason = main.send_gate(CONFIG, now=_at(6))
        assert ok is False
        assert "before" in reason

    def test_sends_at_or_after_hour_when_not_yet_sent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "STATE_PATH", tmp_path / "last_sent")
        ok, reason = main.send_gate(CONFIG, now=_at(7))
        assert ok is True
        assert reason == "ok"

    def test_blocks_when_already_sent_today(self, tmp_path, monkeypatch):
        state = tmp_path / "last_sent"
        state.write_text("2026-07-27\n")
        monkeypatch.setattr(main, "STATE_PATH", state)
        ok, reason = main.send_gate(CONFIG, now=_at(9))
        assert ok is False
        assert "already sent" in reason


class TestSeenCache:
    def test_prunes_entries_older_than_retention(self, tmp_path, monkeypatch):
        seen = tmp_path / "seen.json"
        recent = datetime.now(UTC).date().isoformat()
        old = (datetime.now(UTC) - timedelta(days=main.SEEN_RETENTION_DAYS + 5)).date().isoformat()
        seen.write_text(json.dumps({"https://x/recent": recent, "https://x/old": old}))
        monkeypatch.setattr(main, "SEEN_PATH", seen)

        loaded = main._load_seen()
        assert "https://x/recent" in loaded
        assert "https://x/old" not in loaded

    def test_ignores_malformed_values(self, tmp_path, monkeypatch):
        seen = tmp_path / "seen.json"
        seen.write_text(json.dumps({"u": 12345}))  # non-str value
        monkeypatch.setattr(main, "SEEN_PATH", seen)
        assert main._load_seen() == {}

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "SEEN_PATH", tmp_path / "nope.json")
        assert main._load_seen() == {}

    def test_record_then_load_roundtrip(self, tmp_path, monkeypatch):
        seen = tmp_path / "seen.json"
        monkeypatch.setattr(main, "SEEN_PATH", seen)
        main._record_seen(["https://x/a", "https://x/b"])
        loaded = main._load_seen()
        assert set(loaded) == {"https://x/a", "https://x/b"}
