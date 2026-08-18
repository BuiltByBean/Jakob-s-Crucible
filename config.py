"""Configuration for The Wisdom Crucible site.

Single Config class, values from os.environ with safe local defaults
(house pattern from Data-Dungeon/Talent Booker). Production env vars:
SECRET_KEY, DATA_DIR (volume mount), DATABASE_URL (optional Postgres),
MAIL_* + CONTACT_RECIPIENT for the contact form, TWC_FORCE_HTTPS=1.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# SQLite-on-a-volume: mount a Railway volume at /data and set DATA_DIR=/data
# so the DB survives redeploys; unset locally it lives in ./data.
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _normalise_db_url(url: str) -> str:
    # Railway/Heroku hand out postgres:// which SQLAlchemy 2.x rejects.
    return url.replace("postgres://", "postgresql://", 1)


def _env_bool(key: str, default: str = "0") -> bool:
    return os.environ.get(key, default).strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-wisdom-crucible-local")
    # A production deploy running on the dev fallback key must be loud — but
    # never raise: a hard raise on missing env once 502-looped a prod deploy
    # elsewhere. Logged CRITICAL so it screams from the deploy logs.
    if SECRET_KEY == "dev-wisdom-crucible-local" and (
        os.environ.get("RAILWAY_ENVIRONMENT") or _env_bool("TWC_FORCE_HTTPS")
    ):
        import logging as _logging

        _logging.critical(
            "SECRET_KEY env var is NOT SET in what looks like production — "
            "sessions and CSRF tokens are signed with the public dev key. Set SECRET_KEY now."
        )

    # Bound request bodies: the largest legitimate POST is the contact form
    # (~8KB of text). Without a cap, Werkzeug 3.x accepts unlimited bodies.
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024

    SQLALCHEMY_DATABASE_URI = _normalise_db_url(
        os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'wisdom_crucible.db'}")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        # Harmless on SQLite; sized for gunicorn gthread on hosted Postgres
        # (workers x threads concurrent conversations, recycle under the
        # platform's ~5 min idle kill, fail fast instead of hanging).
        "pool_pre_ping": True,
        "pool_recycle": 280,
        "pool_timeout": 10,
    }

    # Cookies (public site has no login, but the session carries flashes + CSRF).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("TWC_FORCE_HTTPS")

    # CSRF: token tied to session, no expiry — a contact form left open
    # overnight should not 400 on submit.
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_SSL_STRICT = False

    # Contact form email (Flask-Mail over SMTP). Unset MAIL_USERNAME locally =
    # messages are stored in the DB and logged, nothing sent, nothing errors.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USE_TLS = _env_bool("MAIL_USE_TLS", "1")
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get(
        "MAIL_DEFAULT_SENDER", os.environ.get("MAIL_USERNAME", "") or "noreply@thewisdomcrucible.com"
    )
    CONTACT_RECIPIENT = os.environ.get("CONTACT_RECIPIENT", "thewisdomcrucible@gmail.com")

    # Ministry identity — single source of truth for templates.
    MINISTRY_NAME = "The Wisdom Crucible"
    MINISTRY_TAGLINE = "Where wisdom distills transformation through the Word of God."
    MINISTRY_EMAIL = "thewisdomcrucible@gmail.com"
    MINISTRY_PHONE = "254-242-1486"
    YOUTUBE_URL = "https://www.youtube.com/@TheWisdomCrucible"
    PODCAST_URL = "https://www.youtube.com/playlist?list=PLBAJ0RIFzc-9-xeVEBgu8yFGoIik2Ehgw"
    TWITTER_URL = "https://x.com/WisdomCrucible"
