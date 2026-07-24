"""Send the digest email via Resend."""

from __future__ import annotations

import os

import resend


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
    print(f"  Sent via Resend (id: {resp.get('id', '?')})")
