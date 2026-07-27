"""Entry point: collect → curate → render → send."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .collect import collect_all
from .curate import curate
from .render import render_html, render_text
from .send import send_email, send_failure_alert

log = logging.getLogger("digest")

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config.example.yaml"
STATE_PATH = REPO_ROOT / "state" / "last_sent"
SEEN_PATH = REPO_ROOT / "state" / "seen_urls.json"
SEEN_RETENTION_DAYS = 21  # how long a sent item stays suppressed from repeats
PLACEHOLDER_EMAIL = "you@example.com"  # must be changed before a real send


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def load_config() -> dict:
    """Load config.yaml, falling back to the committed config.example.yaml.

    config.yaml is gitignored and personal; a fresh clone (or a CI run before
    the CONFIG_YAML variable is materialised) still loads the example so the
    error message is clear rather than a bare FileNotFoundError."""
    path = CONFIG_PATH if CONFIG_PATH.is_file() else EXAMPLE_CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not config:
        raise RuntimeError(f"{path.name} is empty or invalid YAML")
    if config.get("email", {}).get("to") == PLACEHOLDER_EMAIL:
        raise RuntimeError(
            "config email.to is still the placeholder. Set your own config: copy "
            "config.example.yaml to config.yaml and edit it (or set the CONFIG_YAML "
            "repo variable for GitHub Actions)."
        )
    return config


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


def _load_seen() -> dict[str, str]:
    """Return {url_key: iso_date} of recently-sent items, pruned to retention.

    Lets us widen the collection lookback (to bridge the arXiv weekend gap)
    without re-sending the same item on consecutive days."""
    try:
        data = json.loads(SEEN_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {}
    cutoff = (datetime.now(UTC) - timedelta(days=SEEN_RETENTION_DAYS)).date().isoformat()
    return {k: v for k, v in data.items() if isinstance(v, str) and v >= cutoff}


def _record_seen(keys: list[str]) -> None:
    seen = _load_seen()
    today = datetime.now(UTC).date().isoformat()
    for k in keys:
        seen[k] = today
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=0, sort_keys=True))


def send_gate(config: dict, now: datetime | None = None) -> tuple[bool, str]:
    """Decide whether to send now. Returns (send?, reason).

    Robust to GitHub cron jitter/drops: the workflow fires several times through
    the morning; we send on the FIRST fire at/after the target local hour that
    hasn't already sent today, and record a per-day marker so later fires no-op.
    DST-safe because we compare against local (Europe/London) date & hour.

    `now` is injectable for testing; it defaults to the current local time."""
    st = config["send_time"]
    now_local = now or datetime.now(ZoneInfo(st["timezone"]))
    today = now_local.date().isoformat()
    if now_local.hour < st["hour"]:
        return False, f"before {st['hour']}:00 {st['timezone']}"
    if _last_sent() == today:
        return False, f"already sent today ({today})"
    return True, "ok"


def run(config: dict, args: argparse.Namespace) -> int:
    if not args.force and not args.dry_run:
        ok, reason = send_gate(config)
        if not ok:
            log.info("Skipping — %s.", reason)
            return 0

    log.info("Collecting candidates…")
    items = collect_all(config)

    seen = _load_seen()
    if seen:
        before = len(items)
        items = [it for it in items if it.dedup_key() not in seen]
        if before != len(items):
            log.info("Skipped %d already-sent items", before - len(items))

    log.info("Curating…")
    result = curate(items, config)
    featured, more = result["featured"], result["more"]
    log.info("Selected %d featured + %d more", len(featured), len(more))

    today = datetime.now(ZoneInfo(config["send_time"]["timezone"]))
    html = render_html(featured, more, today)
    text = render_text(featured, more, today)
    subject = f"{config['email']['subject_prefix']} — {today.strftime('%a %d %b')}"

    if args.dry_run:
        out = CONFIG_PATH.parent / "digest.html"
        out.write_text(html, encoding="utf-8")
        log.info("Dry run — wrote %s", out)
        print("\n" + text)
        return 0

    log.info("Sending…")
    send_email(config, subject, html, text)
    # Remember what we actually sent so it isn't repeated in later digests.
    _record_seen([sel["item"].dedup_key() for sel in featured + more])
    if not args.force:  # only the scheduled run records the once-per-day marker
        _record_sent(_today_local(config).isoformat())
    log.info("Done.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build and send the daily digest.")
    ap.add_argument("--force", action="store_true", help="ignore the send-time gate")
    ap.add_argument("--dry-run", action="store_true", help="write digest.html locally, do not send")
    args = ap.parse_args()

    _configure_logging()
    config = load_config()

    try:
        return run(config, args)
    except Exception:
        # Unattended job: log the traceback, alert, and exit non-zero so CI
        # surfaces the failure rather than passing silently.
        log.exception("Digest run failed")
        if not args.dry_run:
            import traceback

            send_failure_alert(config, traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
