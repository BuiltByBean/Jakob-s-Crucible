"""Public pages: home, about, teaching library, series, episode, resources, contact."""
from __future__ import annotations

import re
import threading
import time

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

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
    starting_points = []
    for t, why in ((intro, "Meet Jakob and hear what the crucible is for."),
                   (statement, "The doctrinal foundation, laid out at length."),
                   (featured, "The flagship study — what faith actually is.")):
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
    teachings = (
        Teaching.query.filter_by(series_id=series.id).order_by(Teaching.sort_order).all()
    )
    return render_template("series.html", series=series, teachings=teachings)


@bp.route("/teachings/<slug>")
def teaching_detail(slug):
    teaching = Teaching.query.filter_by(slug=slug).first_or_404()
    sections = split_description(teaching.description or "")

    related = _related_teachings(teaching)
    transcript = teaching.transcript_segments.all()
    return render_template(
        "teaching.html",
        teaching=teaching,
        sections=sections,
        related=related,
        transcript=transcript,
    )


@bp.route("/statement-of-faith")
def statement_of_faith():
    """Stable URL for the doctrinal-foundation episode (linked from home)."""
    teaching = Teaching.query.filter_by(is_statement_of_faith=True).first()
    if teaching is None:
        abort(404)
    return redirect(url_for("public.teaching_detail", slug=teaching.slug))


@bp.route("/resources")
def resources():
    rows = (
        Resource.query.order_by(Resource.category_order, Resource.category, Resource.sort_order).all()
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

        ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
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
        row.emailed = send_email_safe(
            current_app._get_current_object(),
            subject=f"[The Wisdom Crucible] {subject or 'New contact message'}",
            recipients=[current_app.config["CONTACT_RECIPIENT"]],
            body=body,
        )
        db.session.commit()

        flash("Thank you — your message has been sent.", "success")
        return redirect(url_for("public.contact"))

    return render_template("contact.html", form={})


def _related_teachings(teaching: Teaching, limit: int = 4) -> list[Teaching]:
    """Same series neighbours first, then teachings sharing a scripture book
    or topic. Never includes shorts or the teaching itself."""
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
