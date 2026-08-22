"""Admin authentication — hand-rolled session auth (house pattern).

Two accounts, one privilege level, no self-registration and no password-reset
email (services/mail is a silent no-op when MAIL_USERNAME is unset, so an
email-based recovery flow would fail into silence). Recovery is an operator
action: scripts/reset_admin_password.py.

Sessions are Flask's signed cookies, so there is no server-side store to
revoke. Two things stand in for it:
  * session_epoch — copied into the session at login and compared on every
    request; bumping it on the user row signs out every other device.
  * issued_at / seen_at — absolute and idle expiry enforced per request.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import current_app, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash  # noqa: F401 (re-exported)

from models import AdminUser, LoginAttempt, db

SESSION_KEY_USER = "admin_user_id"
SESSION_KEY_EPOCH = "admin_epoch"
SESSION_KEY_ISSUED = "admin_issued_at"
SESSION_KEY_SEEN = "admin_seen_at"

ABSOLUTE_LIFETIME = timedelta(hours=12)
IDLE_LIFETIME = timedelta(hours=2)

# Throttle: per-IP and per-email, counted over one hour.
THROTTLE_WINDOW = timedelta(hours=1)
MAX_FAILURES_PER_IP = 15
MAX_FAILURES_PER_EMAIL = 8

MIN_PASSWORD_LENGTH = 8


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _naive_utcnow() -> datetime:
    """Columns store naive UTC (house rule: store UTC, convert at display)."""
    return _utcnow().replace(tzinfo=None)


def hash_password(raw: str) -> str:
    return generate_password_hash(raw)


def client_ip() -> str:
    """RIGHTMOST X-Forwarded-For entry — appended by Railway's edge proxy, so
    it is the real peer. The leftmost entry is client-supplied and spoofable."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[-1].strip() or request.remote_addr or "?")[:64]


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------

def _recent_failures(column, value: str) -> int:
    cutoff = _naive_utcnow() - THROTTLE_WINDOW
    return (
        LoginAttempt.query.filter(column == value, LoginAttempt.created_at >= cutoff).count()
    )


def _normalise(email: str) -> str:
    """Throttle keys must match how authenticate() matches: case-INSENSITIVELY.
    Counting 'Owner@x.com' and 'owner@x.com' separately let an attacker defeat
    the per-account cap by varying capitalisation."""
    return (email or "").strip().lower()[:320]


def login_throttled(email: str) -> bool:
    """True when THIS IP has failed too often lately.

    Deliberately keyed on IP alone. An account-wide block is a denial of
    service against the owner: his email address is printed in the site
    footer, so anyone could lock him out of his own admin forever by failing
    a login a few times an hour. The per-account counter still exists, but it
    only ever slows a WRONG password (see account_throttled) — a correct
    password is always honoured."""
    try:
        if _recent_failures(LoginAttempt.ip, client_ip()) >= MAX_FAILURES_PER_IP:
            return True
        return False
    except Exception as exc:  # noqa: BLE001 — a throttle-table failure must not lock the owner out
        db.session.rollback()
        logging.warning("admin: throttle check failed (%s) — allowing attempt", exc)
        return False


def account_under_attack(email: str) -> bool:
    """Many recent failures for this account, from anywhere. Used only to log
    loudly and to slow the response — never to refuse a correct password."""
    try:
        return _recent_failures(LoginAttempt.email, _normalise(email)) >= MAX_FAILURES_PER_EMAIL
    except Exception:  # noqa: BLE001
        db.session.rollback()
        return False


def record_failure(email: str) -> None:
    try:
        db.session.add(LoginAttempt(ip=client_ip(), email=_normalise(email)))
        db.session.commit()
        logging.warning("admin: failed login for %r from %s", _normalise(email), client_ip())
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logging.warning("admin: could not record failed login (%s)", exc)


def clear_failures(email: str) -> None:
    """Successful login wipes that account's failures so a legitimate owner
    who fat-fingered his password a few times isn't slowed afterwards."""
    try:
        LoginAttempt.query.filter(
            db.or_(LoginAttempt.email == _normalise(email), LoginAttempt.ip == client_ip())
        ).delete(synchronize_session=False)
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()


def prune_attempts() -> None:
    cutoff = _naive_utcnow() - THROTTLE_WINDOW
    try:
        LoginAttempt.query.filter(LoginAttempt.created_at < cutoff).delete(synchronize_session=False)
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

def authenticate(email: str, password: str) -> AdminUser | None:
    """Verify credentials. Returns None for unknown email, wrong password, or
    a deactivated account — the caller must not distinguish them to the user."""
    user = AdminUser.query.filter(db.func.lower(AdminUser.email) == (email or "").strip().lower()).first()
    if user is None or not user.is_active or not user.password_hash:
        # Spend comparable time on unknown accounts so response timing does
        # not reveal which emails exist.
        check_password_hash(
            "scrypt:32768:8:1$placeholderplaceholder$" + "0" * 128, password or ""
        )
        return None
    if not check_password_hash(user.password_hash, password or ""):
        return None
    return user


def start_session(user: AdminUser) -> None:
    """Log the user in. session.clear() FIRST: it drops the pre-login
    csrf_token so Flask-WTF mints a fresh one, and prevents an anonymous
    session from being carried into a privileged one."""
    session.clear()
    session.permanent = True
    session[SESSION_KEY_USER] = user.id
    session[SESSION_KEY_EPOCH] = user.session_epoch or 1
    now = _utcnow().timestamp()
    session[SESSION_KEY_ISSUED] = now
    session[SESSION_KEY_SEEN] = now
    user.last_login_at = _naive_utcnow()
    db.session.commit()
    logging.info("admin: %s signed in from %s", user.email, client_ip())


def end_session() -> None:
    session.clear()


def bump_epoch(user: AdminUser) -> None:
    """Invalidate every other device's session (used on password change)."""
    user.session_epoch = (user.session_epoch or 1) + 1


def set_password(user: AdminUser, raw: str) -> None:
    user.password_hash = hash_password(raw)
    user.must_change_password = False
    bump_epoch(user)


def password_problem(raw: str, confirm: str) -> str | None:
    """Human-readable validation message, or None when the password is fine."""
    raw = raw or ""
    if len(raw) < MIN_PASSWORD_LENGTH:
        return f"Please use at least {MIN_PASSWORD_LENGTH} characters."
    if raw != (confirm or ""):
        return "The two passwords didn't match."
    if raw.strip().lower() in ("password", "password1", "12345678", "changeme"):
        return "That password is too easy to guess — please choose another."
    return None


# ---------------------------------------------------------------------------
# Request guard
# ---------------------------------------------------------------------------

def current_admin() -> AdminUser | None:
    """The signed-in user for this request, or None. Cached on g."""
    if "admin_user" in g:
        return g.admin_user

    g.admin_user = None
    user_id = session.get(SESSION_KEY_USER)
    if not user_id:
        return None

    user = db.session.get(AdminUser, user_id)
    if user is None or not user.is_active:
        end_session()
        return None
    # Password changed, signed out elsewhere, or an operator reset it — stale.
    if session.get(SESSION_KEY_EPOCH) != (user.session_epoch or 1):
        end_session()
        return None

    now = _utcnow().timestamp()
    issued = float(session.get(SESSION_KEY_ISSUED) or 0)
    seen = float(session.get(SESSION_KEY_SEEN) or 0)
    if (now - issued) > ABSOLUTE_LIFETIME.total_seconds() or (now - seen) > IDLE_LIFETIME.total_seconds():
        end_session()
        return None
    session[SESSION_KEY_SEEN] = now

    g.admin_user = user
    return user


def admin_enabled() -> tuple[bool, str]:
    """Refuse to arm the admin area on a production deploy that is missing the
    prerequisites — a forgeable session cookie is worse than no admin at all."""
    import os

    cfg = current_app.config
    looks_production = bool(os.environ.get("RAILWAY_ENVIRONMENT")) or cfg.get("SESSION_COOKIE_SECURE")
    if looks_production and cfg.get("SECRET_KEY") == "dev-wisdom-crucible-local":
        return False, "SECRET_KEY is unset in production — admin sessions would be forgeable."
    return True, ""


def login_required(view):
    """Kept for explicit use; the admin blueprint also guards every route in a
    before_request so a newly added route is protected by default."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_admin() is None:
            return redirect(url_for("admin.login", next=safe_next(request.full_path)))
        return view(*args, **kwargs)

    return wrapped


def safe_next(target: str | None) -> str | None:
    """Only same-site, path-only redirects.

    Rejects absolute URLs, scheme-relative '//evil.example', backslash forms
    that browsers normalise to scheme-relative, and control characters."""
    if not target:
        return None
    target = target.strip()
    if not target.startswith("/") or target.startswith("//") or target.startswith("/\\"):
        return None
    if "\\" in target or any(ord(c) < 32 for c in target):
        return None
    if not target.startswith("/admin"):
        return None
    return target[:300]
