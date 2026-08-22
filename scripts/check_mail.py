"""Diagnose contact-form email delivery, and optionally send a real test.

    python scripts/check_mail.py                    # report configuration only
    python scripts/check_mail.py --send             # also send a test message
    railway ssh "python scripts/check_mail.py"      # against production

Mail is deliberately fire-and-forget in the request path (a failed send must
never break the contact form), so the ONLY symptom of a misconfiguration is an
inbox that stays empty. This makes the failure legible.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def run(send: bool = False) -> int:
    from app import create_app
    from models import ContactMessage

    app = create_app()
    cfg = app.config
    user, password = cfg.get("MAIL_USERNAME"), cfg.get("MAIL_PASSWORD")
    recipient = cfg.get("CONTACT_RECIPIENT")

    print("--- configuration ---")
    print(f"  MAIL_SERVER        {cfg.get('MAIL_SERVER')}:{cfg.get('MAIL_PORT')} (TLS={cfg.get('MAIL_USE_TLS')})")
    print(f"  MAIL_USERNAME      {user or '(NOT SET)'}")
    print(f"  MAIL_PASSWORD      {'set (' + str(len(password)) + ' chars)' if password else '(NOT SET)'}")
    print(f"  MAIL_DEFAULT_SENDER{cfg.get('MAIL_DEFAULT_SENDER')!r}")
    print(f"  delivers to        {recipient}")

    with app.app_context():
        total = ContactMessage.query.count()
        sent = ContactMessage.query.filter_by(emailed=True).count()
    print(f"  messages stored    {total} ({sent} had an email attempted)")

    if not user or not password:
        print("\nDIAGNOSIS: email is OFF because MAIL_USERNAME and/or MAIL_PASSWORD are unset.")
        print("Every message is still saved and visible in /admin/messages — nothing is lost —")
        print("but services/mail.send_email_safe returns early without contacting the SMTP server.")
        print("\nTo switch it on, set these on the hosting dashboard and redeploy:")
        print("  MAIL_USERNAME = thewisdomcrucible@gmail.com")
        print("  MAIL_PASSWORD = <a Google APP PASSWORD, not the account password>")
        print("Create an app password at: Google Account > Security > 2-Step Verification > App passwords.")
        print("(Gmail rejects a plain account password over SMTP, which looks identical to a wrong password.)")
        return 1

    print("\nConfiguration looks complete.")
    if not send:
        print("Re-run with --send to deliver a real test message.")
        return 0

    print(f"\nSending a test message to {recipient} ...")
    logging.getLogger().setLevel(logging.INFO)
    import smtplib

    from flask_mail import Message

    from services.mail import mail

    with app.app_context():
        try:
            mail.send(Message(
                subject="[The Wisdom Crucible] Test message",
                recipients=[recipient],
                body="If you are reading this, contact-form notifications are working.",
                reply_to=user,
            ))
        except smtplib.SMTPAuthenticationError as exc:
            print(f"FAILED: the mail server rejected the credentials ({exc.smtp_code}).")
            print("Almost always means MAIL_PASSWORD is an account password rather than an app password.")
            return 2
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED: {type(exc).__name__}: {exc}")
            return 2
    print("SENT — check the inbox (and the spam folder).")
    return 0


if __name__ == "__main__":
    sys.exit(run(send="--send" in sys.argv))
