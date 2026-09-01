"""Tests for the send gate and the seen-URL retention cache."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from digest import main

CONFIG = {"send_time": {"hour": 7, "timezone": "Europe/London", "min_interval_days": 6}}


class TestLoadConfig:
    def test_prefers_config_over_example(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("email:\n  to: real@me.com\n")
        example = tmp_path / "config.example.yaml"
        example.write_text(f"email:\n  to: {main.PLACEHOLDER_EMAIL}\n")
        monkeypatch.setattr(main, "CONFIG_PATH", cfg)
        monkeypatch.setattr(main, "EXAMPLE_CONFIG_PATH", example)
        assert main.load_config()["email"]["to"] == "real@me.com"

    def test_falls_back_to_example_and_rejects_placeholder(self, tmp_path, monkeypatch):
        example = tmp_path / "config.example.yaml"
        example.write_text(f"email:\n  to: {main.PLACEHOLDER_EMAIL}\n")
        monkeypatch.setattr(main, "CONFIG_PATH", tmp_path / "config.yaml")  # missing
        monkeypatch.setattr(main, "EXAMPLE_CONFIG_PATH", example)
        with pytest.raises(RuntimeError, match="placeholder"):
            main.load_config()


def _at(hour: int, plus_days: int = 0) -> datetime:
    # Monday 2026-07-27, optionally offset by whole days.
    base = datetime(2026, 7, 27, hour, 0, tzinfo=ZoneInfo("Europe/London"))
    return base + timedelta(days=plus_days)


class TestSendGate:
    def test_blocks_before_target_hour(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "STATE_PATH", tmp_path / "last_sent")
        ok, reason = main.send_gate(CONFIG, now=_at(6))
        assert ok is False
        assert "before" in reason

    def test_sends_at_or_after_hour_when_never_sent(self, tmp_path, monkeypatch):
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
        assert "0d ago" in reason

    def test_blocks_within_the_interval(self, tmp_path, monkeypatch):
        """The later fires of the same morning, and the Tuesday catch-up, no-op."""
        state = tmp_path / "last_sent"
        state.write_text("2026-07-27\n")  # Monday
        monkeypatch.setattr(main, "STATE_PATH", state)
        ok, reason = main.send_gate(CONFIG, now=_at(9, plus_days=1))  # Tuesday
        assert ok is False
        assert "1d ago" in reason

    def test_sends_once_the_interval_has_elapsed(self, tmp_path, monkeypatch):
        state = tmp_path / "last_sent"
        state.write_text("2026-07-27\n")  # Monday
        monkeypatch.setattr(main, "STATE_PATH", state)
        # The following Monday is 7 days later — past the 6-day interval.
        ok, reason = main.send_gate(CONFIG, now=_at(7, plus_days=7))
        assert ok is True
        assert reason == "ok"

    def test_tuesday_catch_up_sends_when_monday_was_missed(self, tmp_path, monkeypatch):
        """If every Monday slot was dropped, the marker is a week old and Tuesday sends."""
        state = tmp_path / "last_sent"
        state.write_text("2026-07-20\n")  # the previous Monday
        monkeypatch.setattr(main, "STATE_PATH", state)
        ok, reason = main.send_gate(CONFIG, now=_at(9, plus_days=1))  # Tuesday, 8d later
        assert ok is True
        assert reason == "ok"

    def test_unreadable_marker_does_not_block(self, tmp_path, monkeypatch):
        state = tmp_path / "last_sent"
        state.write_text("not-a-date\n")
        monkeypatch.setattr(main, "STATE_PATH", state)
        ok, reason = main.send_gate(CONFIG, now=_at(7))
        assert ok is True
        assert reason == "ok"

    def test_defaults_the_interval_when_config_omits_it(self, tmp_path, monkeypatch):
        state = tmp_path / "last_sent"
        state.write_text("2026-07-26\n")
        monkeypatch.setattr(main, "STATE_PATH", state)
        config = {"send_time": {"hour": 7, "timezone": "Europe/London"}}
        ok, _ = main.send_gate(config, now=_at(7))
        assert ok is False


class TestAlertMarker:
    def test_first_failure_alerts_then_suppresses(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "ALERT_PATH", tmp_path / "last_alert")
        assert main._already_alerted_today("2026-07-27") is False
        main._record_alerted("2026-07-27")
        assert main._already_alerted_today("2026-07-27") is True

    def test_a_new_day_alerts_again(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "ALERT_PATH", tmp_path / "last_alert")
        main._record_alerted("2026-07-27")
        assert main._already_alerted_today("2026-07-28") is False


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
