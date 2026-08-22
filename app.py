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


def _create_all_safely() -> None:
    """db.create_all(), tolerant of the multi-worker startup race.

    create_all checks-then-creates, which is not atomic. gunicorn boots its
    workers simultaneously, so on the first deploy after a new table is added
    two workers can both see it missing and both try to create it — the loser
    raises 'table X already exists' and dies, taking the app down until
    gunicorn retries. Observed in production on the deploy that added the
    admin tables."""
    import time

    for attempt in range(1, 4):
        try:
            db.create_all()
            return
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            if "already exists" in str(exc).lower():
                logging.info("schema: another worker created the tables first — continuing")
                return
            if attempt == 3:
                logging.error("schema: create_all failed after %d attempts: %s", attempt, exc)
                return
            time.sleep(0.4 * attempt)


def create_app(config_cls=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_cls)

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    db.init_app(app)
    mail.init_app(app)

    # REGISTERED BEFORE CSRFProtect ON PURPOSE. Flask runs app-level
    # before_request hooks in registration order, and Flask-WTF's hook reads
    # request.form — which parses the body. Anything that must bound the body
    # has to run before that, and has to work off content_length rather than
    # touching request.form itself.
    @app.before_request
    def _bound_public_bodies():
        limit = app.config["PUBLIC_MAX_CONTENT_LENGTH"]
        if (request.blueprint != "admin"
                and request.content_length is not None
                and request.content_length > limit):
            from werkzeug.exceptions import RequestEntityTooLarge

            raise RequestEntityTooLarge()

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

    # Shaped to what this site actually loads. 'unsafe-eval' is required by
    # Alpine (it compiles x-data/@click expressions with new Function) —
    # without it the mobile drawer and every dropdown die.
    # 'inline-speculation-rules' keeps base.html's prefetch block working.
    # form-action 'self' and base-uri 'none' are the two directives that most
    # directly protect the admin forms.
    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-eval' 'inline-speculation-rules'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https://i.ytimg.com https://*.ytimg.com; "
        "frame-src https://www.youtube-nocookie.com; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; base-uri 'none'; form-action 'self'; object-src 'none'"
    )

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Content-Security-Policy", _CSP)
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
                    # [0-9] not \d: this lives in an f-string, where the escape
                    # became a literal backslash and never matched — so every
                    # numbered list rendered as "1. 1. Item".
                    f"<li>{_inline_md(re.sub(r'^[0-9]{1,3}[.)] ', '', line).strip())}</li>" for line in lines
                )
                out.append(f"<ol>{items}</ol>")
            else:
                out.append(f"<p>{_inline_md(block)}</p>")
        return Markup("\n".join(out))

    def _inline_md(text: str) -> str:
        s = str(escape(text))
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
        s = _inline_links(s)
        return s.replace("\n", "<br>")

    def _inline_links(escaped: str) -> str:
        """[label](/path) -> an anchor. Runs over ALREADY-ESCAPED text, and the
        href is re-checked against the scheme allowlist, so an admin-authored
        'javascript:' link can never reach the page."""
        from services.site_content import safe_url

        def _replace(match):
            label, target = match.group(1), match.group(2)
            href = safe_url(target.replace("&amp;", "&"))
            if not href:
                return label
            external = href.startswith("http")
            attrs = ' target="_blank" rel="noopener"' if external else ""
            return f'<a href="{escape(href)}"{attrs}>{label}</a>'

        return re.sub(r"\[([^\]\n]{1,120})\]\(([^)\s]{1,400})\)", _replace, escaped)

    @app.template_filter("rich_text")
    def rich_text(text):
        """One paragraph of admin-authored prose: escaped first, then **bold**,
        *italic*, and [links](/path). Never emits raw HTML."""
        return Markup(_inline_md(text or ""))

    @app.template_filter("safe_url")
    def safe_url_filter(value):
        """Defence in depth at render time: a URL stored by an older code path
        or edited straight in the database still cannot become javascript:."""
        from services.site_content import safe_url

        return safe_url(value) or "#"

    @app.context_processor
    def _globals():
        from datetime import datetime, timezone

        from services import site_content as sc

        # MINISTRY reads through the content registry: every value is
        # admin-editable and falls back to the shipped config default.
        return {
            "ASSET_V": ASSET_V,
            "now_year": datetime.now(timezone.utc).year,
            "content": sc.content,
            "site_links": sc.links,
            "effect_on": sc.enabled,
            "site_number": sc.number,
            "site_pages": sc.page_list,
            "PAGE_GROUPS": sc.GROUPS,
            "MINISTRY": {
                "name": app.config["MINISTRY_NAME"],
                "tagline": sc.content("ministry.tagline"),
                "email": sc.content("ministry.email"),
                "phone": sc.content("ministry.phone"),
                "youtube": sc.content("ministry.youtube_url"),
                "youtube_community": sc.content("ministry.youtube_community_url"),
                "podcast": sc.content("ministry.podcast_url"),
                "podcast_platforms": [(row["name"], row["url"]) for row in sc.links("ministry.podcast_platforms")],
                "twitter": sc.content("ministry.x_url"),
            },
        }

    # ---- Blueprints ---------------------------------------------------------
    from routes.admin import bp as admin_bp
    from routes.explore import bp as explore_bp
    from routes.public import bp as public_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(explore_bp)
    app.register_blueprint(admin_bp)

    # ---- Health + errors ----------------------------------------------------
    @app.route("/healthz")
    def healthz():
        # Auth-free, DB-free, template-free: platform probes must not depend
        # on a warm database or a working template.
        return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.route("/robots.txt")
    def robots():
        # Politeness, not protection — the admin also sends X-Robots-Tag and a
        # noindex meta, which are what actually cover a URL a crawler found
        # some other way.
        body = (
            "User-agent: *\n"
            "Disallow: /admin\n"
            "Allow: /\n"
        )
        return body, 200, {"Content-Type": "text/plain; charset=utf-8"}

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
            code=413, title="File too large",
            message="That file was too large to accept. Study notes and manuscripts can be up to 25MB.",
        ), 413

    @app.errorhandler(403)
    def forbidden(_e):
        # Reached whenever an admin session expires with a form still open —
        # ordinary for someone writing a long manuscript. Werkzeug's default
        # here is a white page, which is a shipped bug for this owner.
        return _ERROR_PAGE.format(
            code=403, title="Your session has expired",
            message='For safety you are signed out after a while. '
                    '<a href="/admin/login" style="color:#4ab5f6;">Sign in again</a> '
                    'to continue &mdash; use your browser&rsquo;s Back button first if you '
                    'want to copy anything you had typed.',
        ), 403

    @app.errorhandler(503)
    def unavailable(_e):
        return _ERROR_PAGE.format(
            code=503, title="Temporarily unavailable",
            message="This part of the site isn&rsquo;t available right now. Please try again shortly.",
        ), 503

    # ---- DB boot ------------------------------------------------------------
    with app.app_context():
        _create_all_safely()
        ensure_columns(db)
        missing = verify_model_columns(db)
        if missing:
            logging.error(
                "schema drift: %s missing — add to services/schema_migrations.COLUMNS_TO_ADD",
                ", ".join(missing),
            )
        # The statement of faith moves from the repo file into the DB once, so
        # the owner can edit it (a repo file would be overwritten every deploy).
        from services.site_content import seed_statement_of_faith_if_empty

        seed_statement_of_faith_if_empty()

        # Log where the database actually resolved to. If this prints a path
        # inside the container instead of the mounted volume, every admin edit
        # (and the owner's password) is silently discarded on the next deploy.
        from config import DATA_DIR

        logging.info("data directory: %s", Path(DATA_DIR).resolve())

        # Scream in the deploy logs if a live account still uses the temporary
        # password. Cheap (a couple of hashes, once per process) and the only
        # thing standing between a public /admin and anyone who guesses it.
        try:
            from werkzeug.security import check_password_hash

            from models import AdminUser

            weak = [u.email for u in AdminUser.query.filter_by(is_active=True).all()
                    if u.password_hash and check_password_hash(u.password_hash, "password")]
            if weak:
                logging.critical(
                    "ADMIN ACCOUNT(S) STILL USING THE TEMPORARY PASSWORD: %s — "
                    "sign in at /admin and change it now.", ", ".join(weak),
                )
        except Exception as exc:  # noqa: BLE001 — never block boot on a warning
            db.session.rollback()
            logging.debug("admin password audit skipped: %s", exc)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")),
            debug=True, use_reloader=False)
