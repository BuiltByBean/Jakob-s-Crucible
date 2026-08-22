"""Configuration for The Wisdom Crucible site.

Single Config class, values from os.environ with safe local defaults
(house pattern from Data-Dungeon/Talent Booker). Production env vars:
SECRET_KEY, DATA_DIR (volume mount), DATABASE_URL (optional Postgres),
MAIL_* + CONTACT_RECIPIENT for the contact form, TWC_FORCE_HTTPS=1.
"""
import os
from datetime import timedelta
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

    # Bound request bodies. The admin uploads Word manuscripts and scanned
    # study-notes PDFs, so the global ceiling is 25MB; every non-admin route is
    # held to PUBLIC_MAX_CONTENT_LENGTH by an app-level before_request that
    # runs BEFORE Flask-WTF's CSRF hook (which parses the body, and would
    # otherwise raise 413 under the smaller cap before any blueprint hook
    # could raise it). Without a cap at all, Werkzeug accepts unlimited bodies.
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024
    PUBLIC_MAX_CONTENT_LENGTH = 1 * 1024 * 1024

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

    # Cookies. The session now carries the admin login, so Secure must not
    # depend on someone remembering to set TWC_FORCE_HTTPS in the dashboard —
    # any production signal turns it on.
    SESSION_COOKIE_NAME = "twc_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("TWC_FORCE_HTTPS") or bool(os.environ.get("RAILWAY_ENVIRONMENT"))
    # Admin sessions carry their own absolute + idle expiry (services/auth);
    # this is the cookie's own lifetime, deliberately a little longer.
    PERMANENT_SESSION_LIFETIME = timedelta(hours=14)

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
    YOUTUBE_COMMUNITY_URL = "https://www.youtube.com/@TheWisdomCrucible/community"
    # Spotify is the podcast's source of truth — every podcast button on the
    # site points here (owner's rule: someone clicking a podcast button is
    # deliberately looking for something other than YouTube).
    PODCAST_URL = "https://open.spotify.com/show/5FKIF6sWeLLaXOhHG6k9BL"
    PODCAST_PLATFORMS = [
        ("Spotify", "https://open.spotify.com/show/5FKIF6sWeLLaXOhHG6k9BL"),
        ("iHeart Radio", "https://www.iheart.com/podcast/269-the-wisdom-crucible-with-j-302846080"),
        ("Amazon Music", "https://music.amazon.com/podcasts/04784b2a-2059-4aaa-9a12-5c4ddbb0482c/the-wisdom-crucible-with-jakob-mcclain"),
        ("Apple Podcasts", "https://podcasts.apple.com/us/podcast/the-wisdom-crucible-with-jakob-mcclain/id1848749337"),
    ]
    TWITTER_URL = "https://x.com/WisdomCrucible"
