"""Build the database from data/seed/*.json (scraped from the YouTube channel).

Idempotent full rebuild of the content mirror: series, teachings, chapters,
transcripts, scripture refs, topics, resources. Data the site owns (manuscripts,
manually-added scripture refs) is captured first and restored after, so a
re-seed can never lose it. Run: python scripts/seed_db.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import bible  # noqa: E402
from services.content import (  # noqa: E402
    extract_scripture_lines,
    parse_timestamp_lines,
    slugify,
    split_description,
    teaching_slug,
)

SEED_DIR = REPO / "data" / "seed"

# Video playlists that ARE the series, in display order. Kind 'shorts' is
# de-emphasised across the site.
SERIES_ORDER = [
    ("Detailed & Verse-by-Verse", "teaching",
     "Methodical, verse-by-verse study that draws truth directly from Scripture — "
     "walking through whole passages and books so the text speaks on its own terms."),
    ("Reflection & Application", "teaching",
     "Reflective messages that connect biblical principles to everyday life through "
     "vivid illustration and thoughtful meditation."),
    ("Questions in the Crucible: Responding to Skeptics", "teaching",
     "Honest engagement with hard questions and objections to Scripture — letting "
     "Scripture speak, and philosophy sit at its feet."),
    ("Shorts", "shorts",
     "Brief sparks from the crucible — short reflections and excerpts from the full teachings."),
]

STATEMENT_OF_FAITH_YT = "_qVItgpBYvg"  # 'What Is Fundamental for The Faith?'
FEATURED_YT = "OD6Jb7sUz8g"  # 'What Is Faith?' — the flagship study

# Curated, deterministic topic assignment (restrained on purpose — a handful of
# themes, never hundreds of tags). New uploads stay unassigned until curated.
TOPICS = [
    ("faith", "Faith & Trust", "What faith is, where it comes from, and what it asks of us."),
    ("doctrine", "Doctrine & Theology", "The foundations of The Faith — what Scripture teaches and why precision matters."),
    ("christology", "Christology", "The person and work of Christ across both testaments."),
    ("apologetics", "Apologetics", "Responding to skeptics and handling objections to Scripture."),
    ("discipleship", "Discipleship", "Following Christ deliberately — teaching, preparation, and perseverance."),
    ("christian-living", "Christian Living", "Priorities, trials, and the daily shape of a life under the Word."),
]
TOPIC_MAP = {
    "faith": ["OD6Jb7sUz8g", "5aSR8NQLifg", "xSesIhhAZ-A"],
    "doctrine": ["_qVItgpBYvg", "QzWQCp8T-QQ", "Y3ySXC-HjwY", "7dqYRBCVeZE"],
    "christology": ["M4bquqXsiyY", "kw2nAbhjAqw", "JkHLTohTOwQ", "mGEcs8slGaI", "Y3ySXC-HjwY"],
    "apologetics": ["QzWQCp8T-QQ", "Cl2TfIEjYmA", "bb65k6s86oE", "YkpogwCLMlI", "lq-mY9sZ5K4"],
    "discipleship": ["1RClTjjUOeM", "ojI5U_s7RqY", "5e_bFOQDnG0", "XVzQhjP1P3w", "sv_VaxKYaZU", "K-uHxvayq6k"],
    "christian-living": ["Qha46IBJoLo", "5vn7Ip6Z60w", "K-uHxvayq6k", "SpSbOn1CBsc", "HmDuPr7HxUE",
                         "KFI38ZdOSWY", "zQdwujoro0Q", "9kltXjyKiUI", "pVJ8yoixVhQ"],
}

# Study Resources page seed — only recommendations the owner has actually named.
RESOURCES = [
    ("Bible Software & Digital Tools", 1, [
        ("Logos Bible Software", "https://www.logos.com/",
         "Deep-study platform for original languages, cross-references, and a serious digital library."),
        ("STEP Bible", "https://www.stepbible.org/",
         "Free web-based study tool from Tyndale House, Cambridge — excellent for comparing "
         "translations and digging into Greek and Hebrew."),
    ]),
    ("Trusted Teaching & Articles", 2, [
        ("The Gospel Coalition", "https://www.thegospelcoalition.org/",
         "Articles, essays, and resources from a broad group of confessional evangelical writers."),
    ]),
]


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def load_seed():
    playlists = json.loads((SEED_DIR / "playlists.json").read_text(encoding="utf-8"))
    videos = json.loads((SEED_DIR / "videos.json").read_text(encoding="utf-8"))
    transcripts = json.loads((SEED_DIR / "transcripts.json").read_text(encoding="utf-8"))
    return playlists, videos, transcripts


def run() -> None:
    from app import create_app
    from models import (
        Chapter, ContactMessage, Resource, ScriptureBook, ScriptureRef, Series,
        Teaching, Topic, TranscriptSegment, db,
    )
    from services import search as search_svc

    app = create_app()
    playlists, videos, transcripts = load_seed()
    by_title = {p["title"]: p for p in playlists}

    with app.app_context():
        # ---- capture site-owned data before the rebuild ---------------------
        preserved_manuscripts: dict[str, str] = {}
        preserved_refs: list[dict] = []
        for t in Teaching.query.all():
            if t.manuscript:
                preserved_manuscripts[t.youtube_id] = t.manuscript
            for r in t.scripture_refs:
                if r.source == "manual":
                    preserved_refs.append({
                        "youtube_id": t.youtube_id, "book_id": r.book_id,
                        "chapter_start": r.chapter_start, "verse_start": r.verse_start,
                        "chapter_end": r.chapter_end, "verse_end": r.verse_end,
                        "is_primary": r.is_primary, "display_text": r.display_text,
                    })

        # ---- wipe content tables (ContactMessage is NOT content — kept) -----
        # The M2M association table isn't a model, so Topic/Teaching
        # query.delete() (raw DELETE, no ORM cascade) never clears it — wipe
        # it explicitly or a re-seed hits the UNIQUE constraint.
        from models import teaching_topics as teaching_topics_table

        db.session.execute(teaching_topics_table.delete())
        for model in (ScriptureRef, Chapter, TranscriptSegment, Teaching, Series,
                      Topic, ScriptureBook, Resource):
            model.query.delete()
        db.session.commit()

        # ---- scripture books ------------------------------------------------
        for number, (name, slug, testament, chapters, _aliases) in enumerate(bible.BOOKS, start=1):
            db.session.add(ScriptureBook(id=number, slug=slug, name=name,
                                         testament=testament, chapters=chapters))
        db.session.commit()

        # ---- series ---------------------------------------------------------
        series_by_title: dict[str, Series] = {}
        for order, (title, kind, description) in enumerate(SERIES_ORDER, start=1):
            pl = by_title.get(title)
            podcast_pl = by_title.get(f"Podcast — {title}") or by_title.get(
                # the podcast playlist capitalises 'By' differently for one series
                next((k for k in by_title if k.lower() == f"podcast — {title}".lower()), ""), None)
            series = Series(
                slug=slugify(title.split(":")[0]),
                title=title,
                description=description,
                kind=kind,
                sort_order=order,
                youtube_playlist_id=(pl or {}).get("id", ""),
                podcast_playlist_id=(podcast_pl or {}).get("id", ""),
            )
            db.session.add(series)
            series_by_title[title] = series
        db.session.commit()

        # ---- podcast pairing: title -> podcast-feed video id ----------------
        # The podcast playlists sometimes contain the MAIN teaching video
        # alongside its dedicated podcast upload under the same title, so only
        # accept dedicated podcast uploads (never tab-listed videos/shorts) as
        # candidates, and iterate sorted so re-seeds are deterministic —
        # set-order + setdefault once silently dropped both Psalm 110 pairings.
        podcast_ids: set[str] = set()
        for p in playlists:
            if p["title"].startswith("Podcast"):
                podcast_ids.update(p["video_ids"])
        podcast_by_title: dict[str, str] = {}
        for vid in sorted(podcast_ids):
            v = videos.get(vid)
            if v and "error" not in v and v.get("source_tab") not in ("videos", "shorts"):
                podcast_by_title.setdefault(_norm_title(v.get("title")), vid)

        # ---- teachings ------------------------------------------------------
        teachings_by_yt: dict[str, Teaching] = {}
        used_slugs: set[str] = set()
        for title, _kind, _d in SERIES_ORDER:
            pl = by_title.get(title)
            if not pl:
                continue
            series = series_by_title[title]
            for position, vid in enumerate(pl["video_ids"], start=1):
                v = videos.get(vid)
                if not v or "error" in v or vid in teachings_by_yt:
                    # duplicate guard: a video filed in two series playlists
                    # keeps its first (display-order) series instead of
                    # crashing the re-seed on the unique youtube_id
                    continue
                t = _build_teaching(v, series, position, podcast_by_title, used_slugs)
                db.session.add(t)
                teachings_by_yt[vid] = t

        # Videos in no video playlist (the channel intro) become standalone.
        for vid, v in videos.items():
            if vid in teachings_by_yt or "error" in v:
                continue
            if v.get("source_tab") == "videos":
                t = _build_teaching(v, None, 0, podcast_by_title, used_slugs)
                db.session.add(t)
                teachings_by_yt[vid] = t
        db.session.commit()

        # ---- chapters, scripture refs, transcripts --------------------------
        for vid, t in teachings_by_yt.items():
            v = videos[vid]
            _add_chapters(db, Chapter, t, v)
            _add_scripture_refs(db, ScriptureRef, t, v)
            seg_data = transcripts.get(vid) or {}
            for seg in seg_data.get("segments", []):
                text = (seg.get("text") or "").strip()
                if text:
                    db.session.add(TranscriptSegment(
                        teaching=t, start_seconds=seg["start"],
                        duration_seconds=seg.get("duration", 0.0), text=text,
                    ))
        db.session.commit()

        # ---- flags ----------------------------------------------------------
        sof = teachings_by_yt.get(STATEMENT_OF_FAITH_YT)
        if sof:
            sof.is_statement_of_faith = True
        feat = teachings_by_yt.get(FEATURED_YT)
        if feat:
            feat.is_featured = True

        # ---- topics ---------------------------------------------------------
        for order, (slug, name, description) in enumerate(TOPICS, start=1):
            topic = Topic(slug=slug, name=name, description=description, sort_order=order)
            for vid in TOPIC_MAP.get(slug, []):
                t = teachings_by_yt.get(vid)
                if t:
                    topic.teachings.append(t)
            db.session.add(topic)

        # ---- resources ------------------------------------------------------
        for category, cat_order, items in RESOURCES:
            for i, (name, url, description) in enumerate(items, start=1):
                db.session.add(Resource(category=category, category_order=cat_order,
                                        name=name, url=url, description=description, sort_order=i))
        db.session.commit()

        # ---- restore site-owned data ---------------------------------------
        for yt_id, manuscript in preserved_manuscripts.items():
            t = teachings_by_yt.get(yt_id)
            if t:
                t.manuscript = manuscript
        for r in preserved_refs:
            t = teachings_by_yt.get(r.pop("youtube_id"))
            if t:
                db.session.add(ScriptureRef(teaching=t, source="manual", **r))
        db.session.commit()

        # ---- search index ---------------------------------------------------
        search_svc.rebuild_index()

        print(f"SEEDED: {Series.query.count()} series, {Teaching.query.count()} teachings, "
              f"{Chapter.query.count()} chapters, {ScriptureRef.query.count()} scripture refs, "
              f"{TranscriptSegment.query.count()} transcript segments, "
              f"{Topic.query.count()} topics, {Resource.query.count()} resources "
              f"({ContactMessage.query.count()} contact messages untouched)")


def _build_teaching(v: dict, series, position: int, podcast_by_title: dict, used_slugs: set):
    from models import Teaching

    title = v.get("title") or v["id"]
    main, _, subtitle = title.partition("|")
    sections = split_description(v.get("description") or "")

    slug = teaching_slug(title)
    if slug in used_slugs:
        slug = f"{slug}-{v['id'][:4].lower()}"
    used_slugs.add(slug)

    published = None
    if v.get("timestamp"):
        published = datetime.fromtimestamp(v["timestamp"], tz=timezone.utc).replace(tzinfo=None)
    elif v.get("upload_date"):
        published = datetime.strptime(v["upload_date"], "%Y%m%d")

    is_short = v.get("source_tab") == "shorts" or (series is not None and series.kind == "shorts")
    podcast_id = ""
    if not is_short:
        candidate = podcast_by_title.get(_norm_title(title), "")
        if candidate != v["id"]:
            podcast_id = candidate

    return Teaching(
        slug=slug,
        title=main.strip(),
        subtitle=subtitle.strip(),
        description=v.get("description") or "",
        summary=sections["summary"],
        kind="short" if is_short else "teaching",
        youtube_id=v["id"],
        podcast_youtube_id=podcast_id,
        notes_url=sections["notes_url"],
        thumbnail_url=v.get("thumbnail") or f"https://i.ytimg.com/vi/{v['id']}/hqdefault.jpg",
        duration_seconds=int(v.get("duration") or 0),
        published_at=published,
        series=series,
        sort_order=position,
    )


def _add_chapters(db, Chapter, t, v: dict) -> None:
    chapters = v.get("chapters") or []
    if chapters:
        for ch in chapters:
            db.session.add(Chapter(teaching=t, start_seconds=int(ch.get("start_time") or 0),
                                   title=(ch.get("title") or "").strip()[:300]))
        return
    sections = split_description(v.get("description") or "")
    for seconds, title in parse_timestamp_lines(sections["timestamps_raw"]):
        db.session.add(Chapter(teaching=t, start_seconds=seconds, title=title[:300]))


def _add_scripture_refs(db, ScriptureRef, t, v: dict) -> None:
    primary_lines, reference_lines = extract_scripture_lines(v.get("description") or "")
    seen: set[tuple] = set()

    def _add(refs: list[bible.ParsedRef], is_primary: bool):
        for ref in refs:
            key = (ref.book_number, ref.chapter_start, ref.verse_start, ref.chapter_end, ref.verse_end)
            if key in seen:
                continue
            seen.add(key)
            db.session.add(ScriptureRef(
                teaching=t, book_id=ref.book_number,
                chapter_start=ref.chapter_start, verse_start=ref.verse_start,
                chapter_end=ref.chapter_end, verse_end=ref.verse_end,
                is_primary=is_primary, display_text=ref.display or bible.format_ref(ref),
                source="auto",
            ))

    for line in primary_lines:
        _add(bible.parse_references(line), True)
    # Titles like 'HAGGAI 1:1–2:9' / 'PSALM 110 Part 3' carry the primary text
    # when the description has no explicit Primary line.
    if not any(True for k in seen):
        _add(bible.parse_references(v.get("title") or ""), True)
    for line in reference_lines:
        _add(bible.parse_references(line), False)


if __name__ == "__main__":
    run()
