"""Send the digest email via Resend."""

from __future__ import annotations

import logging
import os

import resend

log = logging.getLogger(__name__)


def send_email(config: dict, subject: str, html: str, text: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not set")
    resend.api_key = api_key

    email = config["email"]
    resp = resend.Emails.send(
        {
            "from": email["from"],
            "to": [email["to"]],
            "subject": subject,
            "html": html,
            "text": text,
        }
    )
    log.info("Sent via Resend (id: %s)", resp.get("id", "?"))


def send_failure_alert(config: dict, error: str) -> None:
    """Best-effort alert so a broken run doesn't fail silently.

    Never raises: an unattended job that already hit an error shouldn't die
    again inside its own error handler."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        log.warning("Cannot send failure alert: RESEND_API_KEY is not set")
        return
    try:
        resend.api_key = api_key
        email = config["email"]
        resend.Emails.send(
            {
                "from": email["from"],
                "to": [email["to"]],
                "subject": f"{email.get('subject_prefix', 'Weekly Digest')} — FAILED",
                "text": f"The weekly digest run failed:\n\n{error}",
            }
        )
        log.info("Sent failure alert via Resend")
    except Exception as exc:  # noqa: BLE001 - alerting must never raise
        log.error("Failed to send failure alert: %s", exc)
