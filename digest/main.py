"""Entry point: collect → curate → render → send."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .collect import collect_all
from .curate import curate
from .render import render_html, render_text
from .send import send_email

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
STATE_PATH = CONFIG_PATH.parent / "state" / "last_sent"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _today_local(config: dict):
    return datetime.now(ZoneInfo(config["send_time"]["timezone"])).date()


def _last_sent() -> str:
    try:
        return STATE_PATH.read_text().strip()
    except FileNotFoundError:
        return ""


def _record_sent(day: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(day + "\n")


def send_gate(config: dict) -> tuple[bool, str]:
    """Decide whether to send now. Returns (send?, reason).

    Robust to GitHub cron jitter/drops: the workflow fires several times through
    the morning; we send on the FIRST fire at/after the target local hour that
    hasn't already sent today, and record a per-day marker so later fires no-op.
    DST-safe because we compare against local (Europe/London) date & hour."""
    st = config["send_time"]
    now_local = datetime.now(ZoneInfo(st["timezone"]))
    today = now_local.date().isoformat()
    if now_local.hour < st["hour"]:
        return False, f"before {st['hour']}:00 {st['timezone']}"
    if _last_sent() == today:
        return False, f"already sent today ({today})"
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description="Build and send the daily digest.")
    ap.add_argument("--force", action="store_true",
                    help="ignore the send-time gate")
    ap.add_argument("--dry-run", action="store_true",
                    help="write digest.html locally, do not send")
    args = ap.parse_args()

    config = load_config()

    if not args.force and not args.dry_run:
        ok, reason = send_gate(config)
        if not ok:
            print(f"Skipping — {reason}.")
            return 0

    print("Collecting candidates…")
    items = collect_all(config)

    print("Curating with Claude…")
    result = curate(items, config)
    featured, more = result["featured"], result["more"]
    print(f"  Selected {len(featured)} featured + {len(more)} more")

    today = datetime.now(ZoneInfo(config["send_time"]["timezone"]))
    html = render_html(featured, more, today)
    text = render_text(featured, more, today)
    subject = f"{config['email']['subject_prefix']} — {today.strftime('%a %d %b')}"

    if args.dry_run:
        out = CONFIG_PATH.parent / "digest.html"
        out.write_text(html, encoding="utf-8")
        print(f"  Dry run — wrote {out}")
        print("\n" + text)
        return 0

    print("Sending…")
    send_email(config, subject, html, text)
    if not args.force:  # only the scheduled run records the once-per-day marker
        _record_sent(_today_local(config).isoformat())
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
