"""Public pages: home, about, teaching library, series, episode, resources, contact."""
from __future__ import annotations

import re
import threading
import time

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from config import BASE_DIR
from models import ContactMessage, Resource, Series, Teaching, db
from services.content import split_description
from services.mail import send_email_safe

bp = Blueprint("public", __name__)

# Best-effort in-memory per-IP throttle for the contact form (single web
# process — see Procfile worker model). Defense-in-depth on top of the
# honeypot; resets on restart, which is fine for its purpose.
_CONTACT_WINDOW_SECONDS = 3600
_CONTACT_MAX_PER_WINDOW = 5
_contact_hits: dict[str, list[float]] = {}
_contact_lock = threading.Lock()


def _contact_throttled(ip: str) -> bool:
    now = time.time()
    with _contact_lock:
        # Opportunistic global prune: a client rotating spoofed identities
        # must not grow the dict for the life of the process.
        if len(_contact_hits) > 500:
            cutoff = now - _CONTACT_WINDOW_SECONDS
            for stale in [k for k, ts in _contact_hits.items() if all(t < cutoff for t in ts)]:
                del _contact_hits[stale]
        hits = [t for t in _contact_hits.get(ip, []) if now - t < _CONTACT_WINDOW_SECONDS]
        if len(hits) >= _CONTACT_MAX_PER_WINDOW:
            _contact_hits[ip] = hits
            return True
        hits.append(now)
        _contact_hits[ip] = hits
        return False


@bp.route("/")
def home():
    featured = (
        Teaching.query.filter_by(is_featured=True, kind="teaching")
        .order_by(Teaching.published_at.desc())
        .first()
    )
    latest = (
        Teaching.query.filter_by(kind="teaching")
        .order_by(Teaching.published_at.desc())
        .first()
    )
    # If the featured pick IS the latest, don't show it twice.
    if featured is None:
        featured = latest
    latest_is_featured = featured is not None and latest is not None and featured.id == latest.id

    series_list = (
        Series.query.filter_by(kind="teaching").order_by(Series.sort_order).all()
    )
    statement = Teaching.query.filter_by(is_statement_of_faith=True).first()
    recent = (
        Teaching.query.filter_by(kind="teaching")
        .order_by(Teaching.published_at.desc())
        .limit(6)
        .all()
    )
    # Recommended starting points: the channel intro (the one teaching with no
    # series), the Statement of Faith, and the featured flagship study.
    intro = (
        Teaching.query.filter_by(kind="teaching", series_id=None)
        .order_by(Teaching.published_at.asc())
        .first()
    )
    from services.site_content import content as site_text

    starting_points = []
    for t, why in ((intro, site_text("home.start_intro")),
                   (statement, site_text("home.start_statement")),
                   (featured, site_text("home.start_featured"))):
        if t and all(t.id != s[0].id for s in starting_points):
            starting_points.append((t, why))
    return render_template(
        "home.html",
        featured=featured,
        latest_is_featured=latest_is_featured,
        series_list=series_list,
        statement=statement,
        recent=recent,
        starting_points=starting_points,
    )


@bp.route("/about")
def about():
    statement = Teaching.query.filter_by(is_statement_of_faith=True).first()
    return render_template("about.html", statement=statement)


@bp.route("/teachings")
def library():
    """The Teaching Library. Filters: series, kind (teachings default; shorts
    behind an explicit filter so they stay available but de-emphasised)."""
    series_slug = request.args.get("series", "").strip()
    show = request.args.get("show", "teachings").strip()  # teachings | shorts | all
    if show not in ("teachings", "shorts", "all"):
        show = "teachings"

    query = Teaching.query
    active_series = None
    if series_slug:
        active_series = Series.query.filter_by(slug=series_slug).first()
        if active_series is None:
            abort(404)
        query = query.filter_by(series_id=active_series.id)
        # A series page implies its own kind; don't double-filter shorts away.
        if active_series.kind != "shorts" and show != "all":
            query = query.filter_by(kind="teaching")
    elif show == "teachings":
        query = query.filter_by(kind="teaching")
    elif show == "shorts":
        query = query.filter_by(kind="short")

    teachings = query.order_by(Teaching.published_at.desc()).all()
    series_list = Series.query.order_by(Series.sort_order).all()
    return render_template(
        "library.html",
        teachings=teachings,
        series_list=series_list,
        active_series=active_series,
        show=show,
    )


@bp.route("/series/<slug>")
def series_detail(slug):
    series = Series.query.filter_by(slug=slug).first_or_404()
    # Chronological: part 1 before part 2 (playlist order lists newest first).
    teachings = (
        Teaching.query.filter_by(series_id=series.id)
        .order_by(Teaching.published_at.asc())
        .all()
    )
    return render_template("series.html", series=series, teachings=teachings)


@bp.route("/notes/<slug>")
def notes_download(slug):
    """Stable notes URL for episode pages AND YouTube descriptions.

    Serves the self-hosted PDF (drop it at static/notes/<slug>.pdf) when it
    exists; falls back to the external link scraped from the description, so
    the URL can go in a YouTube description today and silently upgrade to
    self-hosting later."""
    from flask import send_from_directory

    from services.site_content import safe_url

    teaching = Teaching.query.filter_by(slug=slug).first_or_404()
    path = teaching.notes_path  # admin upload (volume) first, then committed file
    if path is not None:
        return send_from_directory(
            path.parent, path.name, as_attachment=True,
            download_name=f"{teaching.title} - Study Notes{path.suffix}", conditional=True,
        )
    # Scheme-checked: notes_url is admin-editable, and an unchecked redirect
    # target would make the ministry's domain a phishing hop.
    external = safe_url(teaching.notes_url or "")
    if external:
        return redirect(external)
    abort(404)


@bp.route("/teachings/<slug>")
def teaching_detail(slug):
    from datetime import datetime

    teaching = Teaching.query.filter_by(slug=slug).first_or_404()
    sections = split_description(teaching.description or "")

    related = _related_teachings(teaching)
    # The series rail numbers episodes chronologically (playlist order lists
    # newest first, which read as reversed numbering).
    series_episodes = []
    if teaching.series:
        series_episodes = sorted(
            teaching.series.teachings, key=lambda t: t.published_at or datetime.min
        )
    # The auto-generated transcript stays INDEXED for search but is no longer
    # displayed — the manuscript (Jakob's own script) is the readable form.
    return render_template(
        "teaching.html",
        teaching=teaching,
        sections=sections,
        related=related,
        series_episodes=series_episodes,
    )


@bp.route("/statement-of-faith")
def statement_of_faith():
    """The easy-to-read Statement of Faith, with a pointer to the full
    confession-of-faith episode for the long form.

    The text lives in the database (admin-editable), seeded once at boot from
    content/statement_of_faith.md — a repo file would be overwritten by every
    deploy, so an edit made in the admin would silently vanish."""
    from services.site_content import content as site_text

    body = site_text("statement_of_faith.body")
    if not body:  # first boot before seeding, or the row was cleared
        sof_path = BASE_DIR / "content" / "statement_of_faith.md"
        body = sof_path.read_text(encoding="utf-8") if sof_path.is_file() else ""
        body = re.sub(r"^### Personal Statement of Faith\s*\n", "", body)
    teaching = Teaching.query.filter_by(is_statement_of_faith=True).first()
    # NOT named `content`: that would shadow the global content() helper.
    return render_template("statement_of_faith.html", body=body, teaching=teaching)


@bp.route("/resources")
def resources():
    rows = (
        # Alphabetical within each category (owner's rule).
        Resource.query.order_by(Resource.category_order, Resource.category, Resource.name).all()
    )
    categories: list[tuple[str, list[Resource]]] = []
    for r in rows:
        if not categories or categories[-1][0] != r.category:
            categories.append((r.category, []))
        categories[-1][1].append(r)
    return render_template("resources.html", categories=categories)


@bp.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        # Honeypot: bots that fill the hidden field get the normal success
        # path with no write and no email.
        if request.form.get("website", "").strip():
            flash("Thank you — your message has been sent.", "success")
            return redirect(url_for("public.contact"))

        # Strip + truncate to column bounds (SQLite ignores VARCHAR limits,
        # Postgres raises DataError). Name/subject also get control characters
        # flattened: a bare newline in a header field makes Flask-Mail raise
        # BadHeaderError inside the send thread — the message would be stored
        # but the notification silently dropped.
        def _line(field: str, limit: int) -> str:
            return re.sub(r"[\r\n\t]+", " ", request.form.get(field, "")).strip()[:limit]

        name = _line("name", 200)
        email = _line("email", 320)
        subject = _line("subject", 300)
        message = request.form.get("message", "").strip()[:8000]

        # RIGHTMOST X-Forwarded-For entry: appended by Railway's edge proxy,
        # so it's the real peer address. The leftmost entry is client-supplied
        # and trivially spoofed — keying the throttle on it would let a
        # rotating header bypass the cap entirely.
        ip = (request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
              or request.remote_addr or "?")
        if _contact_throttled(ip):
            flash("You've sent several messages recently — please wait a while before sending more.", "error")
            return redirect(url_for("public.contact"))

        if not message or not email:
            flash("Please include your email address and a message.", "error")
            return render_template(
                "contact.html", form={"name": name, "email": email, "subject": subject, "message": message},
            ), 400

        # DB row FIRST — the message must survive a mail failure.
        row = ContactMessage(name=name, email=email, subject=subject, message=message)
        db.session.add(row)
        db.session.commit()

        body = (
            f"New message from the website contact form\n\n"
            f"From: {name or '(no name)'} <{email}>\n"
            f"Subject: {subject or '(no subject)'}\n\n"
            f"{message}\n"
        )
        from services.site_content import content as site_text

        # Admin-editable, falling back to the configured address.
        recipient = site_text("ministry.contact_recipient") or current_app.config["CONTACT_RECIPIENT"]
        row.emailed = send_email_safe(
            current_app._get_current_object(),
            subject=f"[The Wisdom Crucible] {subject or 'New contact message'}",
            recipients=[recipient],
            body=body,
            # Reply in the owner's mail client goes straight to the visitor.
            # email is newline-flattened by _line() above (header-safe).
            reply_to=email,
        )
        db.session.commit()

        flash("Thank you — your message has been sent.", "success")
        return redirect(url_for("public.contact"))

    return render_template("contact.html", form={})


# Video links Jakob writes into a Short's YouTube description — the ONLY
# source of a Short's related teachings.
_YT_LINK_RE = re.compile(r"(?:youtube\.com/(?:watch\?v=|live/)|youtu\.be/)([\w-]{11})")


def _related_teachings(teaching: Teaching, limit: int = 4) -> list[Teaching]:
    """Same series neighbours first, then teachings sharing a scripture book
    or topic. Never includes shorts or the teaching itself.

    Shorts are the exception (owner's rule): a Short shows a related teaching
    ONLY when Jakob explicitly links a full teaching in the Short's YouTube
    description — never generated from shared topics, books, or series."""
    if teaching.kind == "short":
        linked_ids = _YT_LINK_RE.findall(teaching.description or "")
        if not linked_ids:
            return []
        rows = Teaching.query.filter(
            Teaching.youtube_id.in_(linked_ids), Teaching.kind == "teaching"
        ).all()
        by_yt = {t.youtube_id: t for t in rows}
        picked, seen = [], set()
        for vid in linked_ids:  # preserve the description's own order
            t = by_yt.get(vid)
            if t and t.id not in seen:
                seen.add(t.id)
                picked.append(t)
        return picked[:limit]

    picked: list[Teaching] = []
    seen = {teaching.id}

    def _add(candidates):
        for c in candidates:
            if c.id not in seen and c.kind == "teaching":
                picked.append(c)
                seen.add(c.id)
            if len(picked) >= limit:
                return True
        return False

    if teaching.series_id:
        neighbours = (
            Teaching.query.filter_by(series_id=teaching.series_id)
            .order_by(Teaching.sort_order)
            .all()
        )
        if _add(neighbours):
            return picked

    book_ids = {r.book_id for r in teaching.scripture_refs}
    if book_ids:
        from models import ScriptureRef  # local import avoids cycle at module load

        rows = (
            Teaching.query.join(ScriptureRef)
            .filter(ScriptureRef.book_id.in_(book_ids))
            .order_by(Teaching.published_at.desc())
            .all()
        )
        if _add(rows):
            return picked

    from datetime import datetime

    for topic in teaching.topics:
        ordered = sorted(topic.teachings, key=lambda t: t.published_at or datetime.min, reverse=True)
        if _add(ordered):
            return picked
    return picked
