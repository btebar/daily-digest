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


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def should_run_now(config: dict) -> bool:
    """DST-safe gate: GitHub cron runs in UTC and does not shift for BST/GMT,
    so the workflow fires at two UTC times and we only proceed at the one that
    is actually the target local hour in the configured timezone."""
    st = config["send_time"]
    now_local = datetime.now(ZoneInfo(st["timezone"]))
    return now_local.hour == st["hour"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Build and send the daily digest.")
    ap.add_argument("--force", action="store_true",
                    help="ignore the send-time gate")
    ap.add_argument("--dry-run", action="store_true",
                    help="write digest.html locally, do not send")
    args = ap.parse_args()

    config = load_config()

    if not args.force and not args.dry_run and not should_run_now(config):
        st = config["send_time"]
        print(f"Not {st['hour']}:00 {st['timezone']} yet — skipping.")
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
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
