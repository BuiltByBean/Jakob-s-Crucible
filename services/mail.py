"""Fire-and-forget email sending (Flask-Mail over SMTP).

House pattern: render everything BEFORE spawning the thread, re-enter
app_context inside the thread, log success AND failure, never raise into the
request. With MAIL_USERNAME unset (local dev) nothing sends and nothing errors.
"""
from __future__ import annotations

import logging
import threading

from flask_mail import Mail, Message

mail = Mail()


def send_email_safe(app, *, subject: str, recipients: list[str], body: str,
                    reply_to: str | None = None) -> bool:
    """Queue an email on a daemon thread. Returns whether a send was attempted.

    reply_to: the visitor's address, so a plain Reply in the owner's mail
    client reaches the visitor (without it, Reply targets the ministry's own
    authenticated From address). Callers must pass it newline-flattened."""
    if not app.config.get("MAIL_USERNAME"):
        logging.info("Email skipped (MAIL_USERNAME unset): subject=%r to=%s", subject, recipients)
        return False

    msg = Message(subject=subject, recipients=recipients, body=body, reply_to=reply_to or None)

    def _send():
        try:
            with app.app_context():
                mail.send(msg)
            logging.info("Email sent: subject=%r to=%s", subject, recipients)
        except Exception as exc:  # noqa: BLE001 — mail failure must never surface
            logging.error("Email send failed: subject=%r to=%s error=%s", subject, recipients, exc)

    threading.Thread(target=_send, daemon=True).start()
    return True
