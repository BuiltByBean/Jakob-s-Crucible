"""Re-check a video against YouTube without a full channel sync.

scripts/sync_youtube.py re-scrapes the whole channel, but it needs the dev
dependencies (yt-dlp), writes repo files, and takes minutes — none of which a
web request can do. This module is the runtime subset the owner can trigger
from /admin when he replaces a thumbnail or re-uploads a video.

What actually works from the production container was measured, not assumed
(2026-08-26, from the Railway datacenter IP):

  * oEmbed              -> 200, title + "does this video still exist"   OK
  * i.ytimg.com/vi/...  -> 200, the thumbnail bytes                     OK
  * the watch page      -> 200 but a bot wall: no description, and the
                           embedded videoId is an unrelated promo clip   NO
  * InnerTube /player   -> LOGIN_REQUIRED from a datacenter IP           NO

The same two scrapes succeed from a home connection, which is exactly the
trap: descriptions cannot be refreshed here unless YOUTUBE_API_KEY is set
(the official Data API answers from any IP). So the default path refreshes
thumbnails, titles and availability, and says plainly that descriptions still
come from a full sync — rather than silently doing nothing.

Storage rule, as with notes and site images: refreshed thumbnails live on the
DATA_DIR volume, never under static/ (the container is rebuilt from git on
every deploy). scripts/seed_db.py prefers the volume copy, so a re-sync keeps
the refreshed picture.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from models import SiteContent, db

# A browser UA: i.ytimg.com serves a smaller/older image to obvious scripts.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
_TIMEOUT = 20

# Best first: a replaced thumbnail updates every variant, but only maxres is
# worth showing on a wide card.
_THUMB_VARIANTS = ("maxresdefault", "sddefault", "hqdefault")
_MAX_THUMB_BYTES = 4 * 1024 * 1024
_JPEG_MAGIC = b"\xff\xd8\xff"

# YouTube ids are 11 chars of this alphabet. Enforced before any id reaches a
# filesystem path or a URL.
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,16}$")

_STATE_PREFIX = "youtube."
_SWEEP_KEY = "youtube.sweep"
# A sweep whose worker died (deploy mid-run) must not block the button forever.
_SWEEP_STALE_SECONDS = 15 * 60


# ---------------------------------------------------------------------------
# Small persistent state, in site_content (same trick as the image slots)
# ---------------------------------------------------------------------------

def _read_json(key: str) -> dict:
    row = SiteContent.query.filter_by(key=key).first()
    if row is None or not (row.value or "").strip():
        return {}
    try:
        loaded = json.loads(row.value)
    except (ValueError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_json(key: str, payload: dict, editor: str = "") -> None:
    row = SiteContent.query.filter_by(key=key).first()
    if row is None:
        row = SiteContent(key=key)
        db.session.add(row)
    row.value = json.dumps(payload)
    row.updated_by = (editor or "")[:320]
    db.session.commit()


def state(youtube_id: str) -> dict:
    return _read_json(_STATE_PREFIX + youtube_id)


def _save_state(youtube_id: str, changes: dict, editor: str = "") -> dict:
    merged = state(youtube_id)
    merged.update(changes)
    _write_json(_STATE_PREFIX + youtube_id, merged, editor)
    return merged


def forget(youtube_id: str) -> None:
    SiteContent.query.filter_by(key=_STATE_PREFIX + youtube_id).delete()


def missing_video_ids() -> set[str]:
    """Every video a re-check found was gone from YouTube — in one query, not
    one per episode."""
    gone: set[str] = set()
    rows = SiteContent.query.filter(SiteContent.key.like(_STATE_PREFIX + "%")).all()
    for row in rows:
        if row.key == _SWEEP_KEY:
            continue
        try:
            payload = json.loads(row.value or "{}")
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("missing"):
            gone.add(row.key[len(_STATE_PREFIX):])
    return gone


# ---------------------------------------------------------------------------
# Thumbnails on the volume
# ---------------------------------------------------------------------------

def thumbs_dir() -> Path:
    from config import DATA_DIR

    path = Path(DATA_DIR) / "thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stored_thumb(youtube_id: str) -> tuple[Path, str] | None:
    """(path, version) for a refreshed thumbnail, or None to use the default."""
    if not VIDEO_ID_RE.match(youtube_id or ""):
        return None
    info = state(youtube_id)
    version = str(info.get("thumb_v") or "")
    if not version:
        return None
    path = thumbs_dir() / f"{youtube_id}.jpg"
    return (path, version) if path.is_file() else None


def committed_thumb(youtube_id: str) -> Path | None:
    """The thumbnail baked into the container by scripts/fetch_thumbnails.py."""
    from config import BASE_DIR

    path = Path(BASE_DIR) / "static" / "img" / "thumbs" / f"{youtube_id}.jpg"
    return path if path.is_file() else None


def _showing_digest(youtube_id: str) -> str | None:
    """sha256 of the picture the site is showing RIGHT NOW, or None.

    "Changed" has to mean changed from what visitors see, not merely absent
    from the volume — otherwise the first re-check reports all 30-odd videos
    as updated when nothing about them actually moved."""
    info = state(youtube_id)
    if info.get("thumb_sha") and (thumbs_dir() / f"{youtube_id}.jpg").is_file():
        return str(info["thumb_sha"])
    committed = committed_thumb(youtube_id)
    return hashlib.sha256(committed.read_bytes()).hexdigest() if committed else None


def thumb_url(youtube_id: str) -> str | None:
    """The site-relative URL of a refreshed thumbnail, version-stamped.

    The stamp is the whole point: static/img/thumbs URLs carry no version, so
    without it a replaced picture stays replaced only for people with a cold
    cache."""
    found = stored_thumb(youtube_id)
    return f"/media/thumb/{youtube_id}?v={found[1]}" if found else None


# ---------------------------------------------------------------------------
# Talking to YouTube
# ---------------------------------------------------------------------------

@dataclass
class VideoInfo:
    youtube_id: str
    reachable: bool = False        # we got a definite answer
    missing: bool = False          # definite answer: it is gone / private
    title: str = ""                # full YouTube title, "Main | Subtitle"
    description: str | None = None  # only via the Data API (see module docs)
    duration: int | None = None
    published: datetime | None = None
    error: str = ""


def _get(url: str, timeout: int = _TIMEOUT) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def fetch_oembed(youtube_id: str) -> VideoInfo:
    """Title + "does it still exist", from YouTube's public oEmbed endpoint.

    No key, no quota, and it answers from a datacenter IP — the one YouTube
    surface that does. A deleted or private video answers 400/401/404, which
    is how a removed upload is detected."""
    info = VideoInfo(youtube_id=youtube_id)
    if not VIDEO_ID_RE.match(youtube_id or ""):
        info.error = "That is not a YouTube video id."
        return info
    watch = urllib.parse.quote(f"https://www.youtube.com/watch?v={youtube_id}", safe="")
    try:
        status, body = _get(f"https://www.youtube.com/oembed?url={watch}&format=json")
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403, 404):
            info.reachable = True
            info.missing = True
            return info
        info.error = f"YouTube answered {exc.code}."
        return info
    except Exception as exc:  # noqa: BLE001 — a refresh must never 500
        info.error = f"Could not reach YouTube ({type(exc).__name__})."
        return info

    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        info.error = "YouTube sent an answer we could not read."
        return info
    if status != 200 or not data.get("title"):
        info.error = "YouTube sent an answer we could not read."
        return info
    info.reachable = True
    info.title = str(data.get("title") or "")
    return info


def fetch_api(youtube_id: str, api_key: str) -> VideoInfo:
    """Title, description, duration and publish date from the Data API.

    Optional: only used when YOUTUBE_API_KEY is configured. This is the only
    way to refresh a description from the server (see the module docstring)."""
    info = VideoInfo(youtube_id=youtube_id)
    if not VIDEO_ID_RE.match(youtube_id or ""):
        info.error = "That is not a YouTube video id."
        return info
    url = ("https://www.googleapis.com/youtube/v3/videos"
           f"?part=snippet,contentDetails&id={urllib.parse.quote(youtube_id)}"
           f"&key={urllib.parse.quote(api_key)}")
    try:
        _status, body = _get(url)
        data = json.loads(body)
    except urllib.error.HTTPError as exc:
        info.error = f"The YouTube API rejected the request ({exc.code})."
        return info
    except Exception as exc:  # noqa: BLE001
        info.error = f"Could not reach the YouTube API ({type(exc).__name__})."
        return info

    items = data.get("items") or []
    if not items:
        # The API returns an empty list for deleted AND private videos.
        info.reachable = True
        info.missing = True
        return info
    snippet = items[0].get("snippet") or {}
    info.reachable = True
    info.title = str(snippet.get("title") or "")
    info.description = str(snippet.get("description") or "")
    published = str(snippet.get("publishedAt") or "")
    if published:
        try:
            info.published = datetime.fromisoformat(
                published.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            pass
    info.duration = _parse_iso_duration(
        str((items[0].get("contentDetails") or {}).get("duration") or ""))
    return info


_DURATION_RE = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def _parse_iso_duration(value: str) -> int | None:
    match = _DURATION_RE.match(value or "")
    if not match:
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def api_key() -> str:
    from flask import current_app

    return (current_app.config.get("YOUTUBE_API_KEY") or "").strip()


def fetch(youtube_id: str) -> VideoInfo:
    """Best available answer: the Data API when configured, else oEmbed."""
    key = api_key()
    if key:
        info = fetch_api(youtube_id, key)
        if info.reachable or info.error:
            return info
    return fetch_oembed(youtube_id)


def refresh_thumbnail(youtube_id: str, editor: str = "") -> str:
    """Re-download the thumbnail. Returns 'updated' | 'unchanged' | 'failed'.

    Compares bytes, not URLs — YouTube keeps the same URL when the picture
    behind it changes, which is exactly the case this exists for."""
    if not VIDEO_ID_RE.match(youtube_id or ""):
        return "failed"
    data = None
    for variant in _THUMB_VARIANTS:
        try:
            status, body = _get(f"https://i.ytimg.com/vi/{youtube_id}/{variant}.jpg")
        except Exception:  # noqa: BLE001 — a missing maxres is the normal case
            continue
        # YouTube answers a missing variant with a 120x90 grey placeholder,
        # not a 404, on some edges; the real ones are comfortably bigger.
        if status == 200 and body.startswith(_JPEG_MAGIC) and 2000 < len(body) <= _MAX_THUMB_BYTES:
            data = body
            break
    if data is None:
        return "failed"

    digest = hashlib.sha256(data).hexdigest()
    if _showing_digest(youtube_id) == digest:
        # Byte for byte what visitors already see. Record that we looked, but
        # do not copy it onto the volume: there would be nothing to serve from
        # there that static/ is not already serving.
        _save_state(youtube_id, {"thumb_sha": digest, "checked_at": int(time.time())}, editor)
        return "unchanged"

    (thumbs_dir() / f"{youtube_id}.jpg").write_bytes(data)
    _save_state(youtube_id, {
        "thumb_sha": digest,
        # The cache-busting stamp is the CONTENT hash, not the clock: two
        # refreshes inside the same second would otherwise reuse one ?v= and
        # every warm cache would keep showing the old picture.
        "thumb_v": digest[:10],
        "thumb_bytes": len(data),
        "checked_at": int(time.time()),
    }, editor)
    logging.info("youtube refresh: new thumbnail for %s (%d bytes)", youtube_id, len(data))
    return "updated"


# ---------------------------------------------------------------------------
# Applying a refresh to one teaching
# ---------------------------------------------------------------------------

@dataclass
class RefreshResult:
    youtube_id: str
    title: str = ""
    changed: list[str] = field(default_factory=list)
    missing: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.missing


def refresh_teaching(teaching, editor: str = "", *, reindex: bool = True) -> RefreshResult:
    """Re-check one episode against YouTube and apply what we can read."""
    result = RefreshResult(youtube_id=teaching.youtube_id, title=teaching.title)
    info = fetch(teaching.youtube_id)

    if info.missing:
        _save_state(teaching.youtube_id, {"missing": True, "checked_at": int(time.time())}, editor)
        result.missing = True
        return result
    if not info.reachable:
        result.error = info.error or "Could not reach YouTube."
        return result

    # It answered, so any earlier "this video is gone" verdict is stale.
    _save_state(teaching.youtube_id, {"missing": False, "checked_at": int(time.time())}, editor)

    if info.title:
        main, _, subtitle = info.title.partition("|")
        main, subtitle = main.strip()[:300], subtitle.strip()[:300]
        if main and (main != teaching.title or subtitle != (teaching.subtitle or "")):
            teaching.title, teaching.subtitle = main, subtitle
            result.changed.append("title")

    # Descriptions are DELIBERATELY not synced here (owner's instruction,
    # 2026-09-22). He edits them by hand on the episode's own page after the
    # first import, and a re-check that quietly overwrote that would throw the
    # edit away — the one thing a "re-check" must never do. The API-key path
    # that used to do it is gone with it.
    description_changed = False
    if info.duration and info.duration != (teaching.duration_seconds or 0):
        teaching.duration_seconds = info.duration
        result.changed.append("length")
    if info.published and info.published != teaching.published_at:
        teaching.published_at = info.published
        result.changed.append("date")

    thumb = refresh_thumbnail(teaching.youtube_id, editor)
    if thumb == "updated":
        result.changed.append("thumbnail")
    if thumb == "failed" and not result.changed:
        result.error = "YouTube did not send a thumbnail."

    # Point the episode at the volume copy whenever there is one, even if this
    # run changed nothing: an older refresh may have downloaded it before the
    # column knew about /media/thumb.
    fresh_url = thumb_url(teaching.youtube_id)
    if fresh_url and fresh_url != teaching.thumbnail_url:
        teaching.thumbnail_url = fresh_url
        if "thumbnail" not in result.changed:
            result.changed.append("thumbnail")

    db.session.commit()
    if description_changed and reindex:
        _reindex()
    result.title = teaching.title
    return result


def _apply_description(teaching, description: str) -> None:
    """Store a new description and re-derive everything read out of it."""
    import bible
    from models import Chapter, ScriptureRef
    from services.content import (
        extract_scripture_lines,
        parse_timestamp_lines,
        split_description,
    )

    teaching.description = description
    sections = split_description(description)
    teaching.summary = sections["summary"]
    teaching.notes_url = sections["notes_url"]

    # Chapters are wholly derived from the description here. (A full sync can
    # also read YouTube's own chapter markers; the Data API does not expose
    # them, so the timestamp lines in the description are what we have.)
    Chapter.query.filter_by(teaching_id=teaching.id).delete()
    for seconds, title in parse_timestamp_lines(sections["timestamps_raw"]):
        db.session.add(Chapter(teaching_id=teaching.id, start_seconds=seconds,
                               title=title[:300]))

    # Only the auto-parsed references are rebuilt — anything added by hand
    # (source != 'auto') is the site's own data and survives, exactly as it
    # does through a full re-seed.
    ScriptureRef.query.filter_by(teaching_id=teaching.id, source="auto").delete()
    primary_lines, reference_lines = extract_scripture_lines(description)
    seen: set[tuple] = set()

    def _add(refs, is_primary: bool) -> None:
        for ref in refs:
            key = (ref.book_number, ref.chapter_start, ref.verse_start,
                   ref.chapter_end, ref.verse_end)
            if key in seen:
                continue
            seen.add(key)
            db.session.add(ScriptureRef(
                teaching_id=teaching.id, book_id=ref.book_number,
                chapter_start=ref.chapter_start, verse_start=ref.verse_start,
                chapter_end=ref.chapter_end, verse_end=ref.verse_end,
                is_primary=is_primary,
                display_text=ref.display or bible.format_ref(ref), source="auto"))

    for line in primary_lines:
        _add(bible.parse_references(line), True)
    if not seen:
        _add(bible.parse_references(teaching.title or ""), True)
    for line in reference_lines:
        _add(bible.parse_references(line), False)


def apply_description(teaching, description: str) -> None:
    """Public alias: services/youtube_discover.py derives a NEW episode's
    summary, chapters and Scripture refs through the same parser a re-check
    uses. Two copies of that logic would drift, and the drift would show as an
    episode whose references depend on how it arrived."""
    _apply_description(teaching, description)


def reindex() -> None:
    """Public alias for the same reason — see apply_description."""
    _reindex()


def _reindex() -> None:
    try:
        from services import search as search_svc

        search_svc.rebuild_index()
    except Exception as exc:  # noqa: BLE001 — search is not worth a 500
        logging.warning("youtube refresh: search reindex failed (%s)", exc)


# ---------------------------------------------------------------------------
# Re-pointing an episode at a re-uploaded video
# ---------------------------------------------------------------------------

def parse_video_id(value: str) -> str | None:
    """Accept a bare id, a watch URL, a youtu.be link, or a /shorts/ link."""
    text = (value or "").strip()
    if not text:
        return None
    if VIDEO_ID_RE.match(text):
        return text
    try:
        parsed = urllib.parse.urlparse(text if "//" in text else "https://" + text)
    except ValueError:
        return None
    host = (parsed.netloc or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        query = urllib.parse.parse_qs(parsed.query or "")
        candidate = (query.get("v") or [""])[0]
        if not candidate:
            parts = [p for p in (parsed.path or "").split("/") if p]
            # /shorts/<id>, /embed/<id>, /live/<id>
            candidate = parts[1] if len(parts) > 1 and parts[0] in (
                "shorts", "embed", "live", "v") else ""
    else:
        return None
    return candidate if VIDEO_ID_RE.match(candidate or "") else None


def repoint(teaching, new_id: str, editor: str = "") -> tuple[str | None, RefreshResult | None]:
    """Move an episode onto a re-uploaded video. Returns (error, result).

    A re-upload gets a NEW video id, so nothing that keys off the old one
    finds it any more: the admin's own durability records, the uploaded study
    notes and the refreshed thumbnail all key on youtube_id. They are carried
    across here, or the next channel sync would quietly undo the move."""
    from models import AdminEdit, Teaching, TranscriptSegment

    old_id = teaching.youtube_id
    if not VIDEO_ID_RE.match(new_id or ""):
        return "That doesn't look like a YouTube link.", None
    if new_id == old_id:
        return "That is already this episode's video.", None
    clash = Teaching.query.filter(Teaching.youtube_id == new_id,
                                  Teaching.id != teaching.id).first()
    if clash is not None:
        return f"That video is already on the site as “{clash.title}”.", None

    # Never move onto a video that isn't there — the usual cause is a typo,
    # and the old id would be unrecoverable from the admin afterwards.
    probe = fetch_oembed(new_id)
    if probe.missing:
        return "YouTube says that video doesn't exist (or isn't public yet).", None
    if not probe.reachable:
        return probe.error or "Could not reach YouTube to check that video.", None

    teaching.youtube_id = new_id
    # The old video's captions describe a video nobody can watch any more.
    TranscriptSegment.query.filter_by(teaching_id=teaching.id).delete()
    teaching.podcast_youtube_id = ""

    # Carry the durability records across.
    for row in AdminEdit.query.filter_by(entity_type="teaching", entity_key=old_id).all():
        row.entity_key = new_id
    for row in AdminEdit.query.filter_by(entity_type="featured", entity_key="*").all():
        try:
            payload = json.loads(row.payload or "{}")
        except (ValueError, TypeError):
            continue
        if payload.get("youtube_id") == old_id:
            payload["youtube_id"] = new_id
            row.payload = json.dumps(payload)
    for row in AdminEdit.query.filter_by(entity_type="topic").all():
        try:
            payload = json.loads(row.payload or "{}")
        except (ValueError, TypeError):
            continue
        ids = payload.get("youtube_ids")
        if isinstance(ids, list) and old_id in ids:
            payload["youtube_ids"] = [new_id if i == old_id else i for i in ids]
            row.payload = json.dumps(payload)

    _move_file(_documents_notes_paths(old_id), new_id)
    old_thumb = thumbs_dir() / f"{old_id}.jpg"
    if old_thumb.is_file():
        old_thumb.unlink()
    forget(old_id)
    db.session.commit()

    logging.info("youtube refresh: %s re-pointed from %s to %s", teaching.slug, old_id, new_id)
    result = refresh_teaching(teaching, editor)
    _reindex()
    return None, result


def _documents_notes_paths(youtube_id: str) -> list[Path]:
    from services import documents

    directory = documents.notes_dir()
    return [p for p in directory.glob(f"{youtube_id}.*") if p.is_file()]


def _move_file(paths: list[Path], new_stem: str) -> None:
    for path in paths:
        target = path.with_name(f"{new_stem}{path.suffix}")
        try:
            path.replace(target)
        except OSError as exc:  # noqa: PERF203 — one file, worth the message
            logging.warning("youtube refresh: could not move %s (%s)", path.name, exc)


# ---------------------------------------------------------------------------
# The whole-library sweep (a background thread; 30+ videos outrun a request)
# ---------------------------------------------------------------------------

def sweep_state() -> dict:
    info = _read_json(_SWEEP_KEY)
    if info.get("running") and int(time.time()) - int(info.get("heartbeat") or 0) > _SWEEP_STALE_SECONDS:
        # The worker died mid-run (a deploy, most likely). Don't jam the button.
        info["running"] = False
        info["error"] = "The re-check stopped early. Please run it again."
    return info


def start_sweep(app, editor: str = "") -> str | None:
    """Kick off a whole-library re-check. Returns an error, or None."""
    from models import Teaching

    if sweep_state().get("running"):
        return "A re-check is already running."

    ids = [t.youtube_id for t in Teaching.query.order_by(Teaching.published_at.desc()).all()]
    if not ids:
        return "There are no videos to re-check."
    _write_json(_SWEEP_KEY, {
        "running": True, "started_at": int(time.time()), "heartbeat": int(time.time()),
        "done": 0, "total": len(ids), "updated": 0, "unchanged": 0,
        "missing": [], "failed": [], "changes": [], "by": editor,
    }, editor)

    def _run() -> None:
        with app.app_context():
            try:
                _sweep(ids, editor)
            except Exception as exc:  # noqa: BLE001 — a thread crash must be visible
                logging.exception("youtube refresh: sweep crashed")
                progress = _read_json(_SWEEP_KEY)
                progress.update({"running": False,
                                 "error": f"The re-check stopped: {type(exc).__name__}."})
                _write_json(_SWEEP_KEY, progress, editor)
            finally:
                db.session.remove()

    threading.Thread(target=_run, daemon=True).start()
    return None


def _sweep(ids: list[str], editor: str) -> None:
    from models import Teaching

    reindex_needed = False
    for done, youtube_id in enumerate(ids, start=1):
        teaching = Teaching.query.filter_by(youtube_id=youtube_id).first()
        progress = _read_json(_SWEEP_KEY)
        if teaching is not None:
            result = refresh_teaching(teaching, editor, reindex=False)
            if "description" in result.changed:
                reindex_needed = True
            if result.missing:
                progress.setdefault("missing", []).append(
                    {"id": youtube_id, "title": teaching.title})
            elif result.error:
                progress.setdefault("failed", []).append(
                    {"id": youtube_id, "title": teaching.title, "error": result.error})
            elif result.changed:
                progress["updated"] = int(progress.get("updated") or 0) + 1
                progress.setdefault("changes", []).append(
                    {"id": youtube_id, "title": result.title, "changed": result.changed})
            else:
                progress["unchanged"] = int(progress.get("unchanged") or 0) + 1
        progress["done"] = done
        progress["heartbeat"] = int(time.time())
        _write_json(_SWEEP_KEY, progress, editor)
        time.sleep(0.2)  # be a polite client; 30-odd videos either way

    if reindex_needed:
        _reindex()
    progress = _read_json(_SWEEP_KEY)
    progress.update({"running": False, "finished_at": int(time.time())})
    _write_json(_SWEEP_KEY, progress, editor)
    logging.info("youtube refresh: sweep finished — %s updated, %s unchanged, %s missing",
                 progress.get("updated"), progress.get("unchanged"),
                 len(progress.get("missing") or []))
