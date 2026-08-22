"""Create the admin accounts (idempotent).

Run once per environment:  python scripts/seed_admin.py
In production:             railway ssh "python scripts/seed_admin.py"

NON-DESTRUCTIVE BY DESIGN: an existing account is never touched, so re-running
this after a deploy can't reset a password back to the temporary one. To reset
a forgotten password deliberately, use scripts/reset_admin_password.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# (email, display name, must_change_password)
#   The owner's temporary password is 'password' and he MUST replace it on
#   first sign-in. The developer account keeps its password for testing.
ACCOUNTS = [
    ("thewisdomcrucible@gmail.com", "Jakob McClain", True),
    ("michaelbean21@gmail.com", "Michael Bean", False),
]
TEMPORARY_PASSWORD = "password"


def run() -> None:
    from app import create_app
    from models import AdminUser, db
    from services.auth import hash_password

    app = create_app()
    with app.app_context():
        created, kept = [], []
        for email, name, must_change in ACCOUNTS:
            existing = AdminUser.query.filter(
                db.func.lower(AdminUser.email) == email.lower()
            ).first()
            if existing is not None:
                kept.append(existing.email)
                continue
            db.session.add(AdminUser(
                email=email, name=name,
                password_hash=hash_password(TEMPORARY_PASSWORD),
                must_change_password=must_change, is_active=True, session_epoch=1,
            ))
            created.append(email)
        db.session.commit()

        for email in created:
            print(f"  CREATED {email} (temporary password: {TEMPORARY_PASSWORD!r})")
        for email in kept:
            print(f"  kept    {email} (unchanged)")
        print(f"ADMIN ACCOUNTS: {AdminUser.query.count()} total")
        if created:
            print("Sign in at /admin — the owner is required to choose a new password immediately.")


if __name__ == "__main__":
    run()
