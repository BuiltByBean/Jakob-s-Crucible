"""Explore routes: Scripture navigation, Topics, and Search."""
from __future__ import annotations

from flask import Blueprint, render_template, request

import bible
from models import ScriptureBook, ScriptureRef, Series, Teaching, Topic, db
from services import search as search_svc

bp = Blueprint("explore", __name__)


@bp.route("/scripture")
def scripture_index():
    """Bible -> testament -> canon division -> book, rendered as a library of
    shelves. A book is lit only when it is the PRIMARY passage of at least one
    full teaching (owner's rule: passing citations don't count as 'opened'),
    and the badge counts only those teachings. The rest stay visible but
    dimmed (honest counts, no 'coming soon')."""
    counts = dict(
        db.session.query(ScriptureRef.book_id, db.func.count(db.func.distinct(ScriptureRef.teaching_id)))
        .join(Teaching, Teaching.id == ScriptureRef.teaching_id)
        .filter(ScriptureRef.is_primary.is_(True), Teaching.kind != "short")
        .group_by(ScriptureRef.book_id)
        .all()
    )
    books = {b.id: b for b in ScriptureBook.query.order_by(ScriptureBook.id).all()}

    # Gold books: fully expounded — the primary passages of full teachings
    # cover EVERY chapter of the book (computed, so completing a book later
    # golds it with no hand-maintenance). Currently: Haggai.
    coverage: dict[int, set[int]] = {}
    primary_refs = (
        ScriptureRef.query.join(Teaching, Teaching.id == ScriptureRef.teaching_id)
        .filter(ScriptureRef.is_primary.is_(True), Teaching.kind != "short")
        .all()
    )
    for ref in primary_refs:
        book = books.get(ref.book_id)
        if book is None:
            continue
        start = ref.chapter_start or 1
        end = ref.chapter_end or ref.chapter_start or book.chapters
        coverage.setdefault(ref.book_id, set()).update(range(start, min(end, book.chapters) + 1))
    fully_expounded = {
        book_id for book_id, chapters in coverage.items()
        if len(chapters) >= books[book_id].chapters
    }

    shelves = [
        {"name": name, "testament": testament,
         "books": [books[n] for n in range(start, end + 1) if n in books]}
        for name, testament, start, end in bible.DIVISIONS
    ]
    ot_shelves = [s for s in shelves if s["testament"] == "OT"]
    nt_shelves = [s for s in shelves if s["testament"] == "NT"]

    def _stats(testament):
        total = sum(1 for b in books.values() if b.testament == testament)
        opened = sum(1 for b in books.values() if b.testament == testament and counts.get(b.id, 0) > 0)
        return {"total": total, "opened": opened}

    return render_template(
        "scripture_index.html",
        ot_shelves=ot_shelves, nt_shelves=nt_shelves, counts=counts,
        ot_stats=_stats("OT"), nt_stats=_stats("NT"),
        fully_expounded=fully_expounded,
    )


@bp.route("/scripture/<slug>")
def scripture_book(slug):
    book = ScriptureBook.query.filter_by(slug=slug).first_or_404()
    refs = (
        ScriptureRef.query.filter_by(book_id=book.id)
        .join(Teaching)
        .order_by(Teaching.published_at.desc())
        .all()
    )
    # Chapter grid: which chapters have coverage (primary vs reference-only).
    chapter_hits: dict[int, str] = {}
    for ref in refs:
        start = ref.chapter_start or 1
        end = ref.chapter_end or ref.chapter_start or book.chapters
        for ch in range(start, min(end, book.chapters) + 1):
            level = "primary" if ref.is_primary else "reference"
            if chapter_hits.get(ch) != "primary":
                chapter_hits[ch] = level

    selected_chapter = request.args.get("chapter", type=int)
    if selected_chapter is not None and not (1 <= selected_chapter <= book.chapters):
        selected_chapter = None

    # Teachings grouped: primary passages first, then cross-references.
    def _teachings_for(refs_subset):
        seen: set[int] = set()
        out = []
        for r in refs_subset:
            if r.teaching_id not in seen:
                seen.add(r.teaching_id)
                out.append((r.teaching, r))
        return out

    if selected_chapter:
        visible = [r for r in refs if r.covers_chapter(selected_chapter)]
    else:
        visible = refs
    primary = _teachings_for([r for r in visible if r.is_primary])
    secondary = _teachings_for([r for r in visible if not r.is_primary and r.teaching_id not in {t.id for t, _ in primary}])

    return render_template(
        "scripture_book.html",
        book=book,
        chapter_hits=chapter_hits,
        selected_chapter=selected_chapter,
        primary=primary,
        secondary=secondary,
    )


@bp.route("/topics")
def topics_index():
    topics = Topic.query.order_by(Topic.name).all()  # alphabetical (owner's rule)
    return render_template("topics.html", topics=topics)


@bp.route("/topics/<slug>")
def topic_detail(slug):
    topic = Topic.query.filter_by(slug=slug).first_or_404()
    teachings = sorted(
        (t for t in topic.teachings),
        key=lambda t: (t.kind == "short", -(t.published_at.timestamp() if t.published_at else 0)),
    )
    return render_template("topic.html", topic=topic, teachings=teachings)


@bp.route("/search")
def search():
    q = request.args.get("q", "").strip()[:200]
    results = None
    if q:
        scripture_refs = search_svc.parse_scripture_query(q)
        scripture_books = []
        for ref in scripture_refs:
            book = db.session.get(ScriptureBook, ref.book_number)
            if book:
                scripture_books.append((book, ref))
        series_matches = Series.query.filter(Series.title.ilike(f"%{q}%")).all()
        topic_matches = Topic.query.filter(Topic.name.ilike(f"%{q}%")).all()
        results = {
            "teachings": search_svc.search_teachings(q),
            "manuscripts": search_svc.search_manuscripts(q),
            "transcripts": search_svc.search_transcripts(q),
            "scripture": scripture_books,
            "series": series_matches,
            "topics": topic_matches,
        }
        results["total"] = (
            len(results["teachings"])
            + len(results["manuscripts"])
            + sum(len(g["hits"]) for g in results["transcripts"])
            + len(results["scripture"])
            + len(results["series"])
            + len(results["topics"])
        )
    return render_template("search.html", q=q, results=results)
