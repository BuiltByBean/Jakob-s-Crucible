"""Find uploads the site does not have yet, and add them — from /admin.

`scripts/sync_youtube.py` is the full channel scrape: yt-dlp, dev deps, repo
files, minutes. It cannot run in a web request and it cannot run from the
production container at all (YouTube bot-walls the datacenter IP). That left
the owner with no way to add a new upload without a developer, which is the
gap this closes.

What works from the production container was MEASURED, not assumed — the same
discipline as youtube_refresh.py, because the watch page returns a convincing
200 bot-wall from there and a naive check passes.

  Measured 2026-09-22:
  * feeds/videos.xml?channel_id=...   -> the 15 newest uploads, with FULL
                                        descriptions (byte-for-byte equal to
                                        what the yt-dlp scrape stored)     OK
  * feeds/videos.xml?playlist_id=...  -> the same shape, per playlist      OK
  * oEmbed / i.ytimg.com              -> as documented in youtube_refresh  OK
  * the watch page / InnerTube        -> bot wall, as documented           NO

PODCAST COPIES. The channel's all-uploads feed (channel_id=UC...) also
carries the podcast-tab uploads that YouTube ingests from the podcast's RSS
feed — an audio duplicate of a main episode under the same title. One of them
reached the home page as "the newest episode" (PYvrYYEcA48, 2026-10). So new
uploads are read from the two TAB feeds instead, the channel's own Videos and
Shorts tabs as uploads playlists:

  * UULF<channel> — the Videos tab (long-form only).
  * UUSH<channel> — the Shorts tab.

  Measured 2026-10-28: UULF held all 15 long-form videos back to the channel
  intro and NONE of the 13 dedicated podcast uploads; the two main videos the
  owner also files in a podcast playlist were in it, as they should be.

The owner's instruction was "ignore anything on a podcast playlist", and the
podcast playlists themselves cannot be used for it: their Atom feeds answer
200 with zero entries (podcast-type playlists, measured 2026-10-28), and they
hold some MAIN videos too, which a literal exclusion would drop. Only a
dedicated podcast upload is a duplicate, and the Videos tab is exactly the
set without them.

Two more consequences of using feeds rather than the API:

  * Each feed carries its 15 most recent entries. With the long-form and
    Shorts tabs read separately, a burst of Shorts can no longer push an
    episode out of view; `scripts/sync_youtube.py` still rebuilds history.
  * The feed carries no duration, so "is this a Short?" is membership of the
    Shorts tab, or of the owner's own Shorts playlist, whose feed is fetched
    anyway alongside the series feeds that place a new episode in its series.

Upsert is keyed on the YouTube id, so running it twice adds nothing the second
time. A video the owner has already re-checked and found GONE from YouTube is
never re-added — otherwise the sync would resurrect exactly what a re-check
just established is dead.
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone

from flask import current_app

from models import Series, Teaching, db

FEED = "https://www.youtube.com/feeds/videos.xml"
_TIMEOUT = 15
# A named agent, as the house rule requires: an anonymous scraper is what gets
# rate-limited first, and this one is identifiable if it ever misbehaves.
_UA = "Mozilla/5.0 (compatible; TheWisdomCrucible/1.0; +https://thewisdomcrucible.com)"

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

# The channel the site is built from. Env-overridable so a fork or a test
# channel does not need a code change, but it is NOT admin-editable: pointing
# the site at a different channel is not site maintenance.
DEFAULT_CHANNEL_ID = "UC2693bLTUhOI3UyUm8TfXSQ"


def channel_id() -> str:
    return (current_app.config.get("YOUTUBE_CHANNEL_ID") or DEFAULT_CHANNEL_ID).strip()


@dataclass
class FeedItem:
    """One entry from a YouTube Atom feed."""

    youtube_id: str
    title: str
    description: str
    published: datetime | None
    thumbnail: str = ""
    # Filled in by find_new(); the feed itself says nothing about either.
    is_short: bool = False
    series: object | None = field(default=None, repr=False)

    @property
    def kind(self) -> str:
        return "short" if self.is_short else "teaching"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read()


def _parse_published(raw: str) -> datetime | None:
    """'2026-09-16T14:02:11+00:00' -> naive UTC (the house storage rule)."""
    try:
        return datetime.fromisoformat((raw or "").replace("Z", "+00:00")).astimezone(
            timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def parse_feed(xml: bytes) -> list[FeedItem]:
    """Atom -> items, newest first. Malformed XML yields nothing rather than
    raising into a request: a feed that changes shape must not 500 /admin."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        logging.warning("youtube discover: unparseable feed (%s)", exc)
        return []
    items: list[FeedItem] = []
    for entry in root.findall("atom:entry", _NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=_NS).strip()
        if not vid:
            continue
        group = entry.find("media:group", _NS)
        title = (entry.findtext("media:title", default="", namespaces=_NS)
                 if group is None else group.findtext("media:title", default="", namespaces=_NS))
        desc = "" if group is None else (group.findtext("media:description", default="", namespaces=_NS) or "")
        thumb = ""
        if group is not None:
            node = group.find("media:thumbnail", _NS)
            if node is not None:
                thumb = node.get("url") or ""
        items.append(FeedItem(
            youtube_id=vid,
            title=(title or entry.findtext("atom:title", default="", namespaces=_NS) or "").strip(),
            description=desc,
            published=_parse_published(entry.findtext("atom:published", default="", namespaces=_NS)),
            thumbnail=thumb,
        ))
    return items


def channel_uploads() -> list[FeedItem]:
    """Every upload, podcast copies included. Kept for diagnostics; the sync
    reads tab_uploads() instead — see the module docstring."""
    return parse_feed(_get(f"{FEED}?channel_id={channel_id()}"))


def _uploads_playlist(prefix: str) -> str:
    """UC<id> -> <prefix><id>: YouTube's per-tab uploads playlists."""
    cid = channel_id()
    return prefix + (cid[2:] if cid.startswith("UC") else cid)


def tab_uploads() -> tuple[list[FeedItem], set[str]]:
    """(newest long-form + newest Shorts, the ids that are Shorts).

    Podcast-tab uploads are in neither feed, which is the point. Raises
    URLError/OSError only if BOTH feeds fail; one empty or unreachable tab
    is logged and the other is still used."""
    items: list[FeedItem] = []
    shorts: set[str] = set()
    failures = 0
    for prefix in ("UULF", "UUSH"):
        try:
            entries = playlist_items(_uploads_playlist(prefix))
        except (urllib.error.URLError, OSError) as exc:
            logging.warning("youtube discover: %s feed unreachable (%s)", prefix, exc)
            failures += 1
            if failures == 2:
                raise
            continue
        if prefix == "UUSH":
            shorts.update(i.youtube_id for i in entries)
        items.extend(entries)
    seen: set[str] = set()
    unique = [i for i in items if not (i.youtube_id in seen or seen.add(i.youtube_id))]
    unique.sort(key=lambda i: i.published or datetime.min, reverse=True)
    return unique, shorts


def playlist_items(playlist_id: str) -> list[FeedItem]:
    return parse_feed(_get(f"{FEED}?playlist_id={playlist_id}"))


def series_by_video() -> tuple[dict[str, Series], set[str]]:
    """Map video id -> Series, plus the set of ids that are Shorts.

    One feed per series. A playlist whose feed is empty or unreachable is
    skipped rather than failing the sync — an unplaceable episode still gets
    added, just without a series, which the owner can set afterwards."""
    placing: dict[str, Series] = {}
    shorts: set[str] = set()
    for series in Series.query.filter(Series.youtube_playlist_id != "").all():
        try:
            entries = playlist_items(series.youtube_playlist_id)
        except (urllib.error.URLError, OSError) as exc:
            logging.warning("youtube discover: playlist %s unreachable (%s)",
                            series.youtube_playlist_id, exc)
            continue
        for item in entries:
            placing.setdefault(item.youtube_id, series)
            if series.kind == "shorts":
                shorts.add(item.youtube_id)
    return placing, shorts


# ---------------------------------------------------------------------------
# What is new
# ---------------------------------------------------------------------------

def find_new() -> list[FeedItem]:
    """Uploads on the channel that the site has no Teaching for.

    Skips anything a re-check has already found GONE from YouTube, and
    anything the owner deleted from the admin: both are deliberately absent,
    and re-adding them every sync is the "local delete resurrected by the next
    pull" trap. Also skips a video already paired as some episode's podcast
    copy, as a second guard behind reading the tab feeds."""
    from services import admin_edits, youtube_refresh

    uploads, tab_shorts = tab_uploads()
    if not uploads:
        return []

    have = set()
    for yid, podcast_id in db.session.query(Teaching.youtube_id, Teaching.podcast_youtube_id).all():
        have.add(yid)
        if podcast_id:
            have.add(podcast_id)
    skip = have | youtube_refresh.missing_video_ids() | admin_edits.deleted_teaching_ids()
    candidates = [i for i in uploads if i.youtube_id not in skip]
    if not candidates:
        return []

    placing, shorts = series_by_video()
    shorts |= tab_shorts
    for item in candidates:
        item.is_short = item.youtube_id in shorts
        item.series = placing.get(item.youtube_id)
    return candidates


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------

def _unique_slug(title: str, youtube_id: str) -> str:
    """Same rule as the full seed: the title's slug, disambiguated by the id."""
    from services.content import teaching_slug

    slug = teaching_slug(title) or youtube_id.lower()
    if Teaching.query.filter_by(slug=slug).first() is not None:
        slug = f"{slug}-{youtube_id[:4].lower()}"
    return slug


def add(item: FeedItem, editor: str = "") -> Teaching:
    """Create one Teaching from a feed item, with everything the description
    implies (summary, chapters, Scripture references) derived exactly the way
    a re-check derives it — one parser, not two."""
    from services import youtube_refresh

    main, _, subtitle = (item.title or item.youtube_id).partition("|")
    series = item.series
    teaching = Teaching(
        slug=_unique_slug(item.title or item.youtube_id, item.youtube_id),
        title=main.strip() or item.youtube_id,
        subtitle=subtitle.strip(),
        kind=item.kind,
        youtube_id=item.youtube_id,
        published_at=item.published,
        thumbnail_url=item.thumbnail or f"https://i.ytimg.com/vi/{item.youtube_id}/hqdefault.jpg",
        series_id=series.id if series is not None else None,
        description="",
    )
    db.session.add(teaching)
    db.session.flush()  # the id the derived rows hang off

    youtube_refresh.apply_description(teaching, item.description or "")

    # Same storage rule as a re-check: the picture goes on the volume, so a
    # re-seed keeps it and we stop hotlinking i.ytimg.com. Storing the bytes
    # is only half of it — every template renders teaching.thumbnail_url
    # directly, so the row has to be pointed at the volume copy too or the new
    # episode hotlinks i.ytimg.com forever. refresh_teaching() does the same.
    try:
        youtube_refresh.refresh_thumbnail(item.youtube_id, editor=editor)
        local = youtube_refresh.thumb_url(item.youtube_id)
        if local:
            teaching.thumbnail_url = local
    except Exception as exc:  # noqa: BLE001 — a picture is not worth failing the add
        logging.warning("youtube discover: thumbnail for %s failed (%s)", item.youtube_id, exc)
    return teaching


def sync(editor: str = "") -> dict:
    """Find and add everything new. Returns a report for the admin screen."""
    from services import youtube_refresh

    report = {"ok": True, "error": "", "found": 0, "added": [], "checked_at": datetime.now(timezone.utc).isoformat()}
    try:
        new_items = find_new()
    except (urllib.error.URLError, OSError) as exc:
        logging.warning("youtube discover: channel feed unreachable (%s)", exc)
        report.update(ok=False, error="YouTube did not answer. Try again in a minute.")
        return report

    report["found"] = len(new_items)
    if not new_items:
        return report

    # Oldest first, so the newest upload ends up with the newest row.
    for item in sorted(new_items, key=lambda i: i.published or datetime.min):
        try:
            teaching = add(item, editor=editor)
            report["added"].append({
                "youtube_id": item.youtube_id,
                "title": teaching.title,
                "kind": teaching.kind,
                "series": item.series.title if item.series is not None else "",
                "slug": teaching.slug,
            })
        except Exception as exc:  # noqa: BLE001 — one bad item must not lose the rest
            db.session.rollback()
            logging.warning("youtube discover: could not add %s (%s)", item.youtube_id, exc)

    db.session.commit()
    if report["added"]:
        youtube_refresh.reindex()
    return report
