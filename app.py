"""The Wisdom Crucible — Flask app factory.

`gunicorn app:app` in production; `python app.py` for local dev (reloader OFF
on purpose: the Werkzeug reloader's second process fights SQLite for the file
lock on Windows).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

from flask import Flask, render_template, request
from flask_wtf import CSRFProtect
from markupsafe import Markup, escape
from sqlalchemy import event

from config import BASE_DIR, Config
from models import db
from services.mail import mail
from services.schema_migrations import ensure_columns, verify_model_columns


def _compute_asset_version() -> str:
    """md5 CONTENT hash of the static css/js/img tree, ?v= cache buster.

    Hash the bytes, never mtimes — every deploy rewrites mtimes (fresh git
    clone), so an mtime hash busts every browser cache on every deploy even
    when nothing changed. The tree is ~150KB; hashing it at boot is free."""
    h = hashlib.md5()
    try:
        for sub in ("css", "js", "img"):
            root = BASE_DIR / "static" / sub
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    h.update(str(path.relative_to(root)).encode())
                    h.update(path.read_bytes())
        return h.hexdigest()[:10]
    except OSError:
        return "0"


ASSET_V = _compute_asset_version()


def create_app(config_cls=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_cls)

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    db.init_app(app)
    mail.init_app(app)
    app.csrf = CSRFProtect(app)

    # SQLite PRAGMAs per-connection (a boot-time one-shot only hits one pooled
    # connection; the event listener hits every one).
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        with app.app_context():
            @event.listens_for(db.engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA busy_timeout=5000")
                # SQLite ships with FK enforcement OFF — without this, every
                # declared ondelete CASCADE/SET NULL is inert.
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    # ---- Jinja filters / globals -------------------------------------------
    @app.template_filter("datefmt")
    def datefmt(value, fmt="%B %-d, %Y"):
        if value is None:
            return ""
        if os.name == "nt":  # %-d is POSIX-only
            fmt = fmt.replace("%-d", "%#d")
        return value.strftime(fmt)

    @app.template_filter("duration")
    def duration(seconds):
        seconds = int(seconds or 0)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @app.template_filter("highlight")
    def highlight(text, query):
        """Escape text, then mark matches. The only place search snippets
        become HTML. FTS snippets carry \\x01/\\x02 sentinels around the terms
        it actually matched (incl. stemmed forms); text without sentinels
        (the non-FTS fallback) gets a term-regex pass instead."""
        raw = text or ""
        if "\x01" in raw:
            escaped = str(escape(raw))
            return Markup(escaped.replace("\x01", "<mark>").replace("\x02", "</mark>"))
        escaped = str(escape(raw))
        terms = [t for t in re.findall(r"[\w']+", query or "") if len(t) > 1]
        if terms:
            # ONE combined pass — sequential per-term substitution let a later
            # term (e.g. 'mark' in a 'gospel mark' query) match inside the
            # <mark> tags a previous pass inserted, corrupting the HTML.
            alternation = "|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True))
            escaped = re.sub(f"(({alternation})\\w*)", r"<mark>\1</mark>", escaped, flags=re.IGNORECASE)
        return Markup(escaped)

    @app.template_filter("nl2br")
    def nl2br(text):
        return Markup("<br>".join(escape(line) for line in (text or "").splitlines()))

    @app.template_filter("manuscript_html")
    def manuscript_html(text):
        """Minimal, safe markdown for manuscripts (spoken essays: headings,
        paragraphs, bold/italic, blockquotes). Everything is escaped first —
        no raw HTML ever passes through, so no dependency and no XSS surface."""
        out: list[str] = []
        for block in re.split(r"\n\s*\n", text or ""):
            block = block.strip()
            if not block:
                continue
            lines = block.splitlines()
            if block.startswith("###"):
                out.append(f"<h3>{_inline_md(block.lstrip('#').strip())}</h3>")
            elif block.startswith("#"):
                out.append(f"<h2>{_inline_md(block.lstrip('#').strip())}</h2>")
            elif block.startswith(">"):
                inner = " ".join(line.lstrip("> ").strip() for line in lines)
                out.append(f"<blockquote>{_inline_md(inner)}</blockquote>")
            elif all(line.startswith("- ") for line in lines):
                items = "".join(f"<li>{_inline_md(line[2:].strip())}</li>" for line in lines)
                out.append(f"<ul>{items}</ul>")
            elif all(re.match(r"\d{1,3}[.)] ", line) for line in lines):
                items = "".join(
                    f"<li>{_inline_md(re.sub(r'^\\d{1,3}[.)] ', '', line).strip())}</li>" for line in lines
                )
                out.append(f"<ol>{items}</ol>")
            else:
                out.append(f"<p>{_inline_md(block)}</p>")
        return Markup("\n".join(out))

    def _inline_md(text: str) -> str:
        s = str(escape(text))
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
        return s.replace("\n", "<br>")

    @app.context_processor
    def _globals():
        from datetime import datetime, timezone

        return {
            "ASSET_V": ASSET_V,
            "now_year": datetime.now(timezone.utc).year,
            "MINISTRY": {
                "name": app.config["MINISTRY_NAME"],
                "tagline": app.config["MINISTRY_TAGLINE"],
                "email": app.config["MINISTRY_EMAIL"],
                "phone": app.config["MINISTRY_PHONE"],
                "youtube": app.config["YOUTUBE_URL"],
                "youtube_community": app.config["YOUTUBE_COMMUNITY_URL"],
                "podcast": app.config["PODCAST_URL"],
                "podcast_platforms": app.config["PODCAST_PLATFORMS"],
                "twitter": app.config["TWITTER_URL"],
            },
        }

    # ---- Blueprints ---------------------------------------------------------
    from routes.explore import bp as explore_bp
    from routes.public import bp as public_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(explore_bp)

    # ---- Health + errors ----------------------------------------------------
    @app.route("/healthz")
    def healthz():
        # Auth-free, DB-free, template-free: platform probes must not depend
        # on a warm database or a working template.
        return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}

    _ERROR_PAGE = (
        '<!doctype html><html style="background:#0a0f1a;color-scheme:dark;">'
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>{title}</title></head>"
        '<body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;'
        "background:#0a0f1a;color:#dbe4f0;font-family:Georgia,serif;text-align:center;padding:2rem;\">"
        '<div><p style="font-size:3rem;margin:0 0 .5rem;color:#4ab5f6;">{code}</p>'
        "<h1 style=\"font-size:1.4rem;margin:0 0 1rem;font-weight:600;\">{title}</h1>"
        '<p style="color:#8fa3bd;max-width:34rem;">{message}</p>'
        '<p><a href="/" style="color:#4ab5f6;">Return to The Wisdom Crucible</a></p></div></body></html>'
    )

    @app.errorhandler(404)
    def not_found(_e):
        # Dependency-free inline HTML: a broken template must not break the
        # error page, and 404 must never recursively fail.
        return _ERROR_PAGE.format(
            code=404, title="Page not found",
            message="That page doesn&rsquo;t exist &mdash; it may have moved as the library grows.",
        ), 404

    @app.errorhandler(500)
    def server_error(_e):
        db.session.rollback()
        return _ERROR_PAGE.format(
            code=500, title="Something went wrong",
            message="The fault is ours, not yours. Please try again in a moment.",
        ), 500

    # Every error surface must stay dark — Werkzeug's default white pages
    # violate the anti-flash rule (400 fires on CSRF failures, e.g. a visitor
    # with cookies blocked submitting the contact form).
    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def csrf_error(_e):
        return _ERROR_PAGE.format(
            code=400, title="Form session expired",
            message="Please go back, refresh the page, and send your message again.",
        ), 400

    @app.errorhandler(400)
    def bad_request(_e):
        return _ERROR_PAGE.format(
            code=400, title="Bad request",
            message="Something about that request didn&rsquo;t make sense. Please go back and try again.",
        ), 400

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return _ERROR_PAGE.format(
            code=405, title="Not allowed",
            message="That page doesn&rsquo;t accept this kind of request.",
        ), 405

    @app.errorhandler(413)
    def payload_too_large(_e):
        return _ERROR_PAGE.format(
            code=413, title="Message too large",
            message="That submission was too large. Please shorten it and try again.",
        ), 413

    # ---- DB boot ------------------------------------------------------------
    with app.app_context():
        db.create_all()
        ensure_columns(db)
        missing = verify_model_columns(db)
        if missing:
            logging.error(
                "schema drift: %s missing — add to services/schema_migrations.COLUMNS_TO_ADD",
                ", ".join(missing),
            )

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")),
            debug=True, use_reloader=False)
