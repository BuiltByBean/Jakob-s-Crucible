"""Reset an admin password — the recovery path for a forgotten password.

There is deliberately no "email me a reset link" flow: mail is a silent no-op
unless MAIL_USERNAME is set, so such a flow would fail into silence exactly
when it is needed. Recovery is an operator action instead.

    python scripts/reset_admin_password.py <email> <new-password>
    railway ssh "python scripts/reset_admin_password.py you@example.com 'a good one'"

The account is forced to choose its own password on the next sign-in, and
every existing session for it is invalidated.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def run(email: str, password: str) -> int:
    from app import create_app
    from models import AdminUser, db
    from services.auth import MIN_PASSWORD_LENGTH, hash_password

    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        return 2

    app = create_app()
    with app.app_context():
        user = AdminUser.query.filter(db.func.lower(AdminUser.email) == email.lower()).first()
        if user is None:
            print(f"No admin account for {email!r}. Existing accounts:")
            for row in AdminUser.query.order_by(AdminUser.id).all():
                print(f"  {row.email}")
            return 1
        user.password_hash = hash_password(password)
        user.must_change_password = True
        user.is_active = True
        user.session_epoch = (user.session_epoch or 1) + 1  # signs out every device
        db.session.commit()
        print(f"Reset {user.email}. They must choose a new password at next sign-in.")
        return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python scripts/reset_admin_password.py <email> <new-password>")
    sys.exit(run(sys.argv[1], sys.argv[2]))
