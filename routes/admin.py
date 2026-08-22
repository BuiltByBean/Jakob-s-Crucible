"""The admin area — everything the owner maintains without a code change.

Guard model: ONE before_request protects every route in the blueprint, so a
route added later is protected by default rather than by remembering a
decorator. It runs: enabled -> authenticated -> password-change -> view.

Every mutation is a POST with a CSRF token; nothing here mutates on GET (the
site's speculationrules block prefetches internal links, which would fire any
GET mutation just by hovering).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request,
    session, url_for,
)

from models import (
    AdminUser, ContactMessage, Resource, Series, Teaching, Topic, db,
)
from services import admin_edits, documents
from services import site_content as sc
from services.auth import (
    account_under_attack, admin_enabled, authenticate, check_password_hash,
    clear_failures, current_admin, end_session, login_throttled,
    password_problem, prune_attempts, record_failure, safe_next, set_password,
    start_session,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")

# Views reachable before the forced password change is done.
_PASSWORD_EXEMPT = {"admin.login", "admin.logout", "admin.change_password"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@bp.before_request
def _guard():
    ok, reason = admin_enabled()
    if not ok:
        logging.error("admin: refusing to serve — %s", reason)
        abort(503)

    if request.endpoint == "admin.login":
        return None

    user = current_admin()
    if user is None:
        if request.method != "GET":
            abort(403)  # never bounce a POST to a login form; the body is gone
        return redirect(url_for("admin.login", next=safe_next(request.full_path)))

    if user.must_change_password and request.endpoint not in _PASSWORD_EXEMPT:
        return redirect(url_for("admin.change_password"))

    return None


@bp.after_request
def _admin_headers(resp):
    # Never indexed, never cached, never framed, no referrer leakage of admin URLs.
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    resp.headers["Cache-Control"] = "no-store, private"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


@bp.app_context_processor
def _admin_context():
    if request.blueprint != "admin":
        return {"admin_user": None, "using_temp_password": False}
    return {
        "admin_user": current_admin(),
        "using_temp_password": bool(session.get("admin_temp_password")),
    }


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_admin() is not None and request.method == "GET":
        return redirect(url_for("admin.dashboard"))

    email = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()[:320]
        password = request.form.get("password") or ""

        # Keyed on IP only. Blocking by account would let anyone lock the owner
        # out of his own site — his email is printed in the footer.
        if login_throttled(email):
            logging.warning("admin: throttled login attempt for %r", email)
            flash("Too many attempts from this connection. Please wait a while and try again.", "error")
            return render_template("admin/login.html", email=email), 429

        user = authenticate(email, password)
        if user is None:
            record_failure(email)
            if account_under_attack(email):
                # A correct password would still be honoured; this is a log
                # line for the owner's deploy logs, not a lockout.
                logging.warning("admin: repeated failures against account %r", email)
                time.sleep(1.5)  # slow a distributed guesser without refusing anyone
            # Deliberately identical for unknown email and wrong password.
            flash("Email or password is incorrect.", "error")
            return render_template("admin/login.html", email=email), 401

        clear_failures(user.email)
        prune_attempts()
        start_session(user)
        # We hold the plaintext exactly once, here — flag a still-default
        # password for the banner rather than hashing on every request.
        if password == "password":
            session["admin_temp_password"] = True
        target = safe_next(request.form.get("next")) or url_for("admin.dashboard")
        return redirect(target)

    return render_template("admin/login.html", email=email)


@bp.route("/logout", methods=["POST"])
def logout():
    # Bump the epoch, not just the local cookie: with signed-cookie sessions,
    # clearing the browser's copy leaves any captured copy of that cookie
    # valid for the rest of its window. Signing out must actually revoke.
    user = current_admin()
    if user is not None:
        user.session_epoch = (user.session_epoch or 1) + 1
        db.session.commit()
    end_session()
    flash("You've been signed out.", "success")
    return redirect(url_for("admin.login"))


@bp.route("/password", methods=["GET", "POST"])
def change_password():
    user = current_admin()
    if request.method == "POST":
        new = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        # Re-authenticate unless this is the forced first-run change (where the
        # user has just proved the temporary password to get here). Without it,
        # a borrowed laptop or a replayed cookie becomes permanent takeover.
        if not user.must_change_password:
            current = request.form.get("current") or ""
            if not check_password_hash(user.password_hash, current):
                flash("That isn't your current password.", "error")
                return render_template("admin/change_password.html"), 401
        problem = password_problem(new, confirm)
        if problem:
            flash(problem, "error")
            return render_template("admin/change_password.html"), 400
        set_password(user, new)
        db.session.commit()
        start_session(user)  # re-issue this device's session at the new epoch
        flash("Your password has been changed. Any other signed-in device has been signed out.", "success")
        return redirect(url_for("admin.dashboard"))
    return render_template("admin/change_password.html")


@bp.route("/account", methods=["GET", "POST"])
def account():
    user = current_admin()
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()[:320]
        if not email or email.count("@") != 1 or "." not in email.split("@")[-1]:
            flash("Please enter a valid email address.", "error")
            return redirect(url_for("admin.account"))
        clash = AdminUser.query.filter(
            db.func.lower(AdminUser.email) == email.lower(), AdminUser.id != user.id
        ).first()
        if clash is not None:
            flash("Another account already uses that email address.", "error")
            return redirect(url_for("admin.account"))
        user.email = email
        user.name = (request.form.get("name") or "").strip()[:120]
        db.session.commit()
        flash("Your sign-in details have been updated.", "success")
        return redirect(url_for("admin.account"))
    return render_template("admin/account.html", user=user, users=AdminUser.query.order_by(AdminUser.id).all())


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@bp.route("/")
def dashboard():
    unread = ContactMessage.query.filter(
        ContactMessage.read_at.is_(None), ContactMessage.archived.isnot(True)
    ).count()
    teachings = Teaching.query.filter_by(kind="teaching").count()
    with_manuscript = Teaching.query.filter(
        Teaching.kind == "teaching", Teaching.manuscript.isnot(None), Teaching.manuscript != ""
    ).count()
    featured = Teaching.query.filter_by(is_featured=True).first()
    recent = (
        ContactMessage.query.filter(ContactMessage.archived.isnot(True))
        .order_by(ContactMessage.created_at.desc()).limit(4).all()
    )
    return render_template(
        "admin/dashboard.html",
        groups=sc.GROUPS, unread=unread, teachings=teachings,
        with_manuscript=with_manuscript, featured=featured, recent=recent,
        resources=Resource.query.count(), topics=Topic.query.count(),
    )


# ---------------------------------------------------------------------------
# Page copy
# ---------------------------------------------------------------------------

@bp.route("/pages/<group>", methods=["GET", "POST"])
def page_group(group):
    if group not in sc.GROUP_LABELS:
        abort(404)
    entries = sc.entries_for(group)
    user = current_admin()

    if request.method == "POST":
        # Validate EVERYTHING before writing anything: saving as we go and then
        # rolling back on the first bad field threw away every good edit on the
        # screen, and the redirect meant the typed text was gone too.
        submitted: dict[str, str] = {}
        errors: dict[str, str] = {}
        for entry in entries:
            if entry.kind == "links":
                value = _links_from_form(entry.key)
                if value is None:
                    continue  # field genuinely absent from this submission
            elif entry.kind == "toggle":
                # A hidden "off" plus a checked "on" both arrive; the checkbox
                # is rendered second, so the last value is the real answer.
                sent = request.form.getlist(entry.key)
                if not sent:
                    continue
                value = "on" if "on" in sent else "off"
            else:
                value = request.form.get(entry.key)
                if value is None:
                    continue
            submitted[entry.key] = value
            problem = sc.validation_error(entry, value)
            if problem:
                errors[entry.key] = problem

        if errors:
            for key, message in errors.items():
                flash(f"{sc.BY_KEY[key].label}: {message}", "error")
            # Re-render with what he typed — never redirect away from unsaved work.
            return render_template(
                "admin/page_group.html", group=group, label=sc.GROUP_LABELS[group],
                entries=entries, sc=sc, preview_url=_PREVIEW_URLS.get(group),
                submitted=submitted, errors=errors,
            ), 400

        for key, value in submitted.items():
            sc.save(key, value, user.email)
        db.session.commit()
        flash("Saved. Your changes are live on the site.", "success")
        return redirect(url_for("admin.page_group", group=group))

    return render_template(
        "admin/page_group.html", group=group, label=sc.GROUP_LABELS[group],
        entries=entries, sc=sc, preview_url=_PREVIEW_URLS.get(group),
        submitted={}, errors={},
    )


_PREVIEW_URLS = {
    "appearance": "public.home",
    "home": "public.home",
    "library": "public.library",
    "scripture": "explore.scripture_index",
    "topics": "explore.topics_index",
    "statement_of_faith": "public.statement_of_faith",
    "resources": "public.resources",
    "about": "public.about",
    "contact": "public.contact",
}


def _links_from_form(key: str) -> str | None:
    """Repeatable name/url rows -> the JSON a 'links' value stores.

    Returns None when the field wasn't part of this submission at all. The
    marker input is rendered OUTSIDE the repeatable rows, so a page whose rows
    failed to render (blocked JS) submits no marker and is treated as "not
    submitted" rather than "the owner deleted every podcast platform".
    An empty list saves as "" so the shipped defaults come back."""
    if request.form.get(f"{key}__present") != "1":
        return None
    names = request.form.getlist(f"{key}__name")
    urls = request.form.getlist(f"{key}__url")
    rows = []
    for name, url in zip(names, urls):
        name, url = name.strip(), url.strip()
        if name or url:
            rows.append({"name": name[:80], "url": url[:400]})
    return json.dumps(rows) if rows else ""


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@bp.route("/resources")
def resources():
    rows = Resource.query.order_by(Resource.category_order, Resource.category, Resource.name).all()
    return render_template("admin/resources.html", resources=rows)


@bp.route("/resources/new", methods=["GET", "POST"])
@bp.route("/resources/<int:resource_id>", methods=["GET", "POST"])
def resource_form(resource_id=None):
    resource = db.session.get(Resource, resource_id) if resource_id else None
    if resource_id and resource is None:
        abort(404)

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:200]
        url = (request.form.get("url") or "").strip()[:400]
        category = (request.form.get("category") or "").strip()[:120] or "Bible Software & Digital Tools"
        description = (request.form.get("description") or "").strip()[:4000]
        if not name:
            flash("Please give the resource a name.", "error")
            return render_template("admin/resource_form.html", resource=resource, form=request.form), 400
        if url and not sc.safe_url(url):
            flash("Please enter a full web address starting with https://", "error")
            return render_template("admin/resource_form.html", resource=resource, form=request.form), 400

        if resource is None:
            resource = Resource(sort_order=(Resource.query.count() or 0) + 1)
            db.session.add(resource)
        resource.name, resource.url = name, url
        resource.category, resource.description = category, description
        resource.category_order = request.form.get("category_order", type=int) or 1
        db.session.flush()
        admin_edits.snapshot_resources(current_admin().email)
        db.session.commit()
        flash(f"Saved “{name}”.", "success")
        return redirect(url_for("admin.resources"))

    return render_template("admin/resource_form.html", resource=resource, form={})


@bp.route("/resources/<int:resource_id>/delete", methods=["POST"])
def resource_delete(resource_id):
    resource = db.session.get(Resource, resource_id)
    if resource is None:
        abort(404)
    name = resource.name
    db.session.delete(resource)
    db.session.flush()
    admin_edits.snapshot_resources(current_admin().email)
    db.session.commit()
    flash(f"Removed “{name}”.", "success")
    return redirect(url_for("admin.resources"))


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

@bp.route("/topics")
def topics():
    rows = Topic.query.order_by(Topic.name).all()
    untopiced = (
        Teaching.query.filter(Teaching.kind == "teaching", ~Teaching.topics.any())
        .order_by(Teaching.published_at.desc()).all()
    )
    return render_template("admin/topics.html", topics=rows, untopiced=untopiced)


@bp.route("/topics/new", methods=["GET", "POST"])
@bp.route("/topics/<int:topic_id>", methods=["GET", "POST"])
def topic_form(topic_id=None):
    topic = db.session.get(Topic, topic_id) if topic_id else None
    if topic_id and topic is None:
        abort(404)
    # Shorts never carry topics (owner's rule), so they are not offered here.
    teachings = (
        Teaching.query.filter_by(kind="teaching").order_by(Teaching.published_at.desc()).all()
    )

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:120]
        if not name:
            flash("Please give the topic a name.", "error")
            return render_template("admin/topic_form.html", topic=topic, teachings=teachings,
                                   form=request.form, selected=set(request.form.getlist("teachings"))), 400
        if topic is None:
            slug = _unique_topic_slug(name)
            topic = Topic(slug=slug)
            db.session.add(topic)
        topic.name = name
        topic.description = (request.form.get("description") or "").strip()[:4000]
        chosen = request.form.getlist("teachings")
        rows = Teaching.query.filter(Teaching.youtube_id.in_(chosen)).all() if chosen else []
        topic.teachings = [t for t in rows if t.kind != "short"]
        db.session.flush()
        admin_edits.record(admin_edits.TOPIC, topic.slug, {
            "name": topic.name, "description": topic.description,
            "sort_order": topic.sort_order or 0,
            "youtube_ids": [t.youtube_id for t in topic.teachings],
        }, current_admin().email)
        db.session.commit()
        flash(f"Saved “{name}”.", "success")
        return redirect(url_for("admin.topics"))

    selected = {t.youtube_id for t in topic.teachings} if topic else set()
    return render_template("admin/topic_form.html", topic=topic, teachings=teachings,
                           form={}, selected=selected)


def _unique_topic_slug(name: str) -> str:
    from services.content import slugify

    base = slugify(name) or "topic"
    slug, n = base, 2
    while Topic.query.filter_by(slug=slug).first() is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


@bp.route("/topics/<int:topic_id>/delete", methods=["POST"])
def topic_delete(topic_id):
    topic = db.session.get(Topic, topic_id)
    if topic is None:
        abort(404)
    name, slug = topic.name, topic.slug
    topic.teachings = []
    db.session.delete(topic)
    # RECORD the deletion rather than forgetting the topic: seed_db rebuilds
    # topics from TOPIC_MAP, so "no record" means "recreate it next sync".
    admin_edits.record(admin_edits.TOPIC, slug, {"deleted": True}, current_admin().email)
    db.session.commit()
    flash(f"Removed the “{name}” topic. The teachings themselves are untouched.", "success")
    return redirect(url_for("admin.topics"))


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

@bp.route("/series", methods=["GET", "POST"])
def series():
    rows = Series.query.order_by(Series.sort_order).all()
    if request.method == "POST":
        editor = current_admin().email
        for s in rows:
            field = f"description__{s.id}"
            if field in request.form:
                s.description = (request.form.get(field) or "").strip()[:4000]
                admin_edits.record(admin_edits.SERIES, s.slug, {"description": s.description}, editor)
        db.session.commit()
        flash("Series descriptions saved.", "success")
        return redirect(url_for("admin.series"))
    return render_template("admin/series.html", series_list=rows)


# ---------------------------------------------------------------------------
# Teachings: manuscripts, notes, featured
# ---------------------------------------------------------------------------

@bp.route("/featured", methods=["GET", "POST"])
def featured():
    """Choose what the home page leads with: one pinned teaching, or always
    the newest. Kept as its own screen because it's the single most visible
    editorial decision on the site."""
    teachings_list = (
        Teaching.query.filter_by(kind="teaching").order_by(Teaching.published_at.desc()).all()
    )

    if request.method == "POST":
        choice = (request.form.get("featured") or "").strip()  # "" = automatic
        if choice and not any(t.youtube_id == choice for t in teachings_list):
            flash("That teaching could not be found.", "error")
            return redirect(url_for("admin.featured"))

        Teaching.query.filter(Teaching.is_featured.is_(True)).update(
            {"is_featured": False}, synchronize_session=False
        )
        picked = None
        if choice:
            picked = Teaching.query.filter_by(youtube_id=choice).first()
            picked.is_featured = True
        # Record BOTH cases, including automatic: seed_db flags its own
        # FEATURED_YT on every re-sync, so "automatic" has to be stated.
        admin_edits.record(admin_edits.FEATURED, "*", {"youtube_id": choice or None},
                           current_admin().email)
        db.session.commit()
        flash(f"“{picked.title}” is now featured on the home page." if picked
              else "The home page will always lead with your newest teaching.", "success")
        return redirect(url_for("admin.featured"))

    current = Teaching.query.filter_by(is_featured=True, kind="teaching").first()
    newest = teachings_list[0] if teachings_list else None
    return render_template("admin/featured.html", teachings=teachings_list,
                           current=current, newest=newest)


@bp.route("/teachings")
def teachings():
    kind = request.args.get("kind", "teaching")
    if kind not in ("teaching", "short"):
        kind = "teaching"
    rows = Teaching.query.filter_by(kind=kind).order_by(Teaching.published_at.desc()).all()
    return render_template("admin/teachings.html", teachings=rows, kind=kind)


@bp.route("/teachings/<int:teaching_id>", methods=["GET", "POST"])
def teaching_form(teaching_id):
    teaching = db.session.get(Teaching, teaching_id)
    if teaching is None:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action") or "manuscript"
        editor = current_admin().email

        if action == "manuscript":
            upload = request.files.get("manuscript_file")
            typed = request.form.get("manuscript")
            if upload is not None and upload.filename:
                # An uploaded document wins over whatever is in the box.
                markdown, error = documents.read_manuscript_upload(upload)
                if error:
                    flash(error, "error")
                    return redirect(url_for("admin.teaching_form", teaching_id=teaching.id))
                teaching.manuscript = markdown
            elif typed is None:
                # No file AND no textarea in the payload: this cannot be a real
                # edit, and treating it as "" once silently destroyed a
                # 170,000-character manuscript in testing.
                flash("Please choose a file, or edit the text below.", "error")
                return redirect(url_for("admin.teaching_form", teaching_id=teaching.id))
            elif not typed.strip() and (teaching.manuscript or "").strip():
                # Emptying the box is destructive and irreversible; make it
                # deliberate, like every other destructive control here.
                if request.form.get("confirm_clear") != "yes":
                    flash("To delete the whole manuscript, tick the confirm box first.", "error")
                    return redirect(url_for("admin.teaching_form", teaching_id=teaching.id))
                teaching.manuscript = ""
            else:
                teaching.manuscript = typed.strip()[:400_000]
            db.session.commit()
            admin_edits.record(admin_edits.TEACHING, teaching.youtube_id,
                               {"manuscript": teaching.manuscript}, editor)
            db.session.commit()
            _reindex_search()
            flash("Manuscript saved." if teaching.manuscript else "Manuscript cleared.", "success")

        elif action == "notes":
            upload = request.files.get("notes_file")
            if upload is None or not upload.filename:
                flash("Please choose a file to upload.", "error")
            else:
                error = documents.save_notes_upload(teaching, upload)
                flash(error or "Study notes uploaded.", "error" if error else "success")

        elif action == "notes_delete":
            # Deleting the uploaded file is not enough: a committed
            # static/notes file would immediately take its place (and cannot be
            # deleted at runtime), so record the removal as well.
            documents.delete_notes(teaching)
            teaching.notes_hidden = True
            db.session.commit()
            admin_edits.record(admin_edits.TEACHING, teaching.youtube_id,
                               {"notes_hidden": True}, editor)
            db.session.commit()
            flash("Study notes removed. The download button is gone from the episode page.", "success")

        elif action == "notes_restore":
            teaching.notes_hidden = False
            db.session.commit()
            admin_edits.record(admin_edits.TEACHING, teaching.youtube_id,
                               {"notes_hidden": False}, editor)
            db.session.commit()
            flash("Study notes restored.", "success")

        elif action == "featured":
            # Same record the dedicated picker writes, so the two agree.
            Teaching.query.filter(Teaching.is_featured.is_(True)).update(
                {"is_featured": False}, synchronize_session=False
            )
            teaching.is_featured = True
            admin_edits.record(admin_edits.FEATURED, "*",
                               {"youtube_id": teaching.youtube_id}, editor)
            db.session.commit()
            flash(f"“{teaching.title}” is now featured on the home page.", "success")

        return redirect(url_for("admin.teaching_form", teaching_id=teaching.id))

    return render_template("admin/teaching_form.html", teaching=teaching,
                           notes_path=teaching.notes_path)


def _reindex_search() -> None:
    """Manuscripts are searchable, so the FTS index must follow an edit."""
    try:
        from services import search as search_svc

        search_svc.rebuild_index()
    except Exception as exc:  # noqa: BLE001 — search is not worth a 500
        logging.warning("admin: search reindex failed (%s)", exc)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@bp.route("/messages")
def messages():
    archived = request.args.get("show") == "archived"
    query = ContactMessage.query
    query = query.filter(ContactMessage.archived.is_(True)) if archived else query.filter(
        ContactMessage.archived.isnot(True)
    )
    rows = query.order_by(ContactMessage.created_at.desc()).all()
    # Opening the inbox marks what is shown as read.
    now = _now()
    for row in rows:
        if row.read_at is None:
            row.read_at = now
    db.session.commit()
    return render_template("admin/messages.html", messages=rows, archived=archived,
                           mail_configured=bool(current_app.config.get("MAIL_USERNAME")))


@bp.route("/messages/<int:message_id>/archive", methods=["POST"])
def message_archive(message_id):
    row = db.session.get(ContactMessage, message_id)
    if row is None:
        abort(404)
    row.archived = not bool(row.archived)
    db.session.commit()
    flash("Message archived." if row.archived else "Message restored.", "success")
    return redirect(url_for("admin.messages", show="archived" if not row.archived else None))
