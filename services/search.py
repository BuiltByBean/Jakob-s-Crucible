"""Site search: SQLite FTS5 over teachings + timestamped transcript segments.

Two contentless FTS5 tables (teaching_fts, segment_fts) whose rowids are the
real tables' primary keys. Transcript hits come back as (teaching, timestamp,
highlighted snippet) — the substrate the long-term clip-search / NL-search
goals build on. On a non-SQLite engine (or FTS5 missing) everything degrades
to ILIKE transparently.

A query that parses as a scripture reference ("Haggai 2", "Psalm 110:1") also
resolves to book/chapter results via bible.parse_references.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import text as sql_text

import bible
from models import Teaching, db

_MAX_TERMS = 12

# Markdown line prefixes: headings, blockquote markers, list markers.
_MD_PREFIX_RE = re.compile(r"^(?:#{1,6}\s+|>\s?|[-*+]\s+|\d{1,3}[.)]\s+)", re.MULTILINE)


def _manuscript_plain(text: str) -> str:
    """Manuscript markdown -> plain prose for the FTS index. snippet() quotes
    the indexed text verbatim, so raw '###'/'*' tokens would show in search
    results (the episode page renders through |manuscript_html instead).
    Only markup is removed — every word survives, so matching is unchanged."""
    if not text:
        return ""
    out = _MD_PREFIX_RE.sub("", text)
    out = re.sub(r"\*{1,3}([^*\n]+?)\*{1,3}", r"\1", out)  # *emphasis* / **bold**
    out = re.sub(r"_{2,3}([^_\n]+?)_{2,3}", r"\1", out)
    return re.sub(r"[ \t]{2,}", " ", out)


def _is_sqlite() -> bool:
    return db.engine.dialect.name == "sqlite"


def fts_available() -> bool:
    if not _is_sqlite():
        return False
    try:
        db.session.execute(sql_text("SELECT 1 FROM teaching_fts LIMIT 1"))
        return True
    except Exception:  # noqa: BLE001
        db.session.rollback()
        return False


def rebuild_index() -> bool:
    """Drop and rebuild both FTS tables from the current DB. Idempotent;
    returns False (logged, not raised) when FTS5 isn't available."""
    if not _is_sqlite():
        logging.info("search: non-SQLite engine, FTS index skipped (ILIKE fallback active)")
        return False
    try:
        db.session.execute(sql_text("DROP TABLE IF EXISTS teaching_fts"))
        db.session.execute(sql_text("DROP TABLE IF EXISTS segment_fts"))
        # Plain (contentful) FTS5 tables: contentless (content='') tables
        # cannot serve snippet()/highlight() — they store no text to quote.
        # The duplicated text is ~1.5MB; snippets are the product.
        db.session.execute(sql_text(
            "CREATE VIRTUAL TABLE teaching_fts USING fts5"
            "(title, subtitle, summary, description, manuscript, tokenize='porter unicode61')"
        ))
        db.session.execute(sql_text(
            "CREATE VIRTUAL TABLE segment_fts USING fts5"
            "(text, tokenize='porter unicode61')"
        ))
        for t in Teaching.query.all():
            db.session.execute(
                sql_text(
                    "INSERT INTO teaching_fts(rowid, title, subtitle, summary, description, manuscript) "
                    "VALUES (:id, :ti, :su, :sm, :de, :ma)"
                ),
                {"id": t.id, "ti": t.title, "su": t.subtitle or "", "sm": t.summary or "",
                 "de": t.description or "", "ma": _manuscript_plain(t.manuscript or "")},
            )
        # Caption segments are only a few words each, so a multi-word query
        # would almost never match inside one. Index WINDOWS of consecutive
        # speech instead (~45s / ~400 chars), rowid = the window's first
        # segment — matches stay timestamp-addressable via that segment.
        rows = db.session.execute(sql_text(
            "SELECT id, teaching_id, start_seconds, text FROM transcript_segments "
            "ORDER BY teaching_id, start_seconds"
        )).fetchall()
        windows = 0
        win_id, win_teaching, win_start, win_text = None, None, 0.0, []
        def _flush():
            nonlocal windows, win_id, win_text
            if win_id is not None and win_text:
                db.session.execute(
                    sql_text("INSERT INTO segment_fts(rowid, text) VALUES (:id, :tx)"),
                    {"id": win_id, "tx": " ".join(win_text)},
                )
                windows += 1
            win_id, win_text = None, []
        for rid, teaching_id, start, seg_text in rows:
            if win_id is None or teaching_id != win_teaching or \
                    start - win_start > 45 or sum(len(s) for s in win_text) > 400:
                _flush()
                win_id, win_teaching, win_start = rid, teaching_id, start
            win_text.append(seg_text or "")
        _flush()
        db.session.commit()
        logging.info("search: FTS index rebuilt (%d teachings, %d segments in %d windows)",
                     Teaching.query.count(), len(rows), windows)
        return True
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logging.warning("search: FTS rebuild failed (%s) — ILIKE fallback active", exc)
        return False


def _fts_query(q: str) -> str:
    """Quote each term so user punctuation can't break MATCH syntax."""
    terms = re.findall(r"[\w']+", q)[:_MAX_TERMS]
    return " ".join(f'"{t}"' for t in terms)


def _like_pattern(q: str) -> str:
    """Escape LIKE wildcards for the non-FTS fallback — otherwise a query of
    '%' or '_' matches every row. Pair with ESCAPE '\\\\'."""
    escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_teachings(q: str, limit: int = 25) -> list[Teaching]:
    """Ranked teaching results (title/summary/description/manuscript)."""
    if fts_available():
        match = _fts_query(q)
        if not match:
            return []
        # char(1)/char(2) sentinels wrap the terms FTS actually matched
        # (incl. stemmed forms); the |highlight filter escapes the snippet
        # first and only then turns sentinels into <mark>.
        rows = db.session.execute(
            sql_text("SELECT rowid FROM teaching_fts WHERE teaching_fts MATCH :m ORDER BY rank LIMIT :n"),
            {"m": match, "n": limit},
        ).fetchall()
        ids = [r[0] for r in rows]
        by_id = {t.id: t for t in Teaching.query.filter(Teaching.id.in_(ids)).all()} if ids else {}
        return [by_id[i] for i in ids if i in by_id]
    like = _like_pattern(q)
    return (
        Teaching.query.filter(
            db.or_(
                Teaching.title.ilike(like, escape="\\"),
                Teaching.subtitle.ilike(like, escape="\\"),
                Teaching.summary.ilike(like, escape="\\"),
                Teaching.description.ilike(like, escape="\\"),
                Teaching.manuscript.ilike(like, escape="\\"),
            )
        )
        .order_by(Teaching.published_at.desc())
        .limit(limit)
        .all()
    )


def search_manuscripts(q: str, limit: int = 10) -> list[dict]:
    """Phrase-level hits inside the manuscripts: [{teaching, snippet}].

    The manuscript (Jakob's own script) can differ slightly from the auto
    captions — indexing both means a phrase is found no matter which form it
    appears in; this surfaces WHERE it appears in the written form. Snippet is
    plain text with char(1)/char(2) sentinels for the |highlight filter (same
    contract as transcript snippets). Under the ILIKE fallback there is no
    snippet machinery — manuscript matches still surface via search_teachings."""
    if not fts_available():
        return []
    match = _fts_query(q)
    if not match:
        return []
    rows = db.session.execute(
        sql_text(
            # Column filter: only rows whose MANUSCRIPT holds the match, and
            # snippet() column 4 quotes from the manuscript text itself.
            "SELECT rowid, snippet(teaching_fts, 4, char(1), char(2), '…', 16) "
            "FROM teaching_fts WHERE teaching_fts MATCH :m ORDER BY rank LIMIT :n"
        ),
        {"m": f"manuscript : ({match})", "n": limit},
    ).fetchall()
    ids = [r[0] for r in rows]
    by_id = {t.id: t for t in Teaching.query.filter(Teaching.id.in_(ids)).all()} if ids else {}
    return [{"teaching": by_id[rid], "snippet": snip} for rid, snip in rows if rid in by_id]


def search_transcripts(q: str, limit: int = 40, per_teaching: int = 3) -> list[dict]:
    """Transcript hits grouped by teaching:
    [{teaching, hits: [{start_seconds, timestamp, snippet}, ...]}, ...]
    snippet is PLAIN TEXT (ellipsised); templates escape it and apply the
    |highlight filter, so no HTML ever originates here."""
    grouped: dict[int, list[dict]] = {}

    if fts_available():
        match = _fts_query(q)
        if not match:
            return []
        # char(1)/char(2) sentinels wrap the terms FTS actually matched
        # (incl. stemmed forms); the |highlight filter escapes the snippet
        # first and only then turns sentinels into <mark>.
        rows = db.session.execute(
            sql_text(
                "SELECT s.teaching_id, s.start_seconds, "
                "snippet(segment_fts, 0, char(1), char(2), '…', 14) "
                "FROM segment_fts JOIN transcript_segments s ON s.id = segment_fts.rowid "
                "WHERE segment_fts MATCH :m ORDER BY rank LIMIT :n"
            ),
            {"m": match, "n": limit * 3},
        ).fetchall()
        for teaching_id, start, snip in rows:
            hits = grouped.setdefault(teaching_id, [])
            if len(hits) < per_teaching:
                hits.append({"start_seconds": int(start), "snippet": snip})
    else:
        like = _like_pattern(q)
        rows = db.session.execute(
            sql_text(
                "SELECT teaching_id, start_seconds, text FROM transcript_segments "
                "WHERE text ILIKE :m ESCAPE '\\' LIMIT :n" if not _is_sqlite() else
                "SELECT teaching_id, start_seconds, text FROM transcript_segments "
                "WHERE text LIKE :m ESCAPE '\\' COLLATE NOCASE LIMIT :n"
            ),
            {"m": like, "n": limit * 3},
        ).fetchall()
        for teaching_id, start, seg_text in rows:
            hits = grouped.setdefault(teaching_id, [])
            if len(hits) < per_teaching:
                hits.append({"start_seconds": int(start), "snippet": "…" + seg_text + "…"})

    ids = list(grouped.keys())[:limit]
    teachings = {t.id: t for t in Teaching.query.filter(Teaching.id.in_(ids)).all()} if ids else {}
    out = []
    for tid in ids:
        t = teachings.get(tid)
        if not t:
            continue
        for h in grouped[tid]:
            secs = h["start_seconds"]
            hh, rem = divmod(secs, 3600)
            mm, ss = divmod(rem, 60)
            h["timestamp"] = f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}"
        out.append({"teaching": t, "hits": grouped[tid]})
    return out


def parse_scripture_query(q: str) -> list[bible.ParsedRef]:
    """Resolve query text to scripture references, if it reads as one."""
    return bible.parse_references(q)
