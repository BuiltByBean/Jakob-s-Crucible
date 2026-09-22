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

Two consequences of using the feed rather than the API:

  * It carries the 15 most recent uploads and no further back. That is a
    catch-up tool, not a backfill — `scripts/sync_youtube.py` is still the
    way to rebuild history.
  * It carries no duration, so "is this a Short?" cannot be read off the
    video. It is decided by membership of the owner's own Shorts playlist,
    whose feed is fetched alongside. He keeps that playlist current (checked:
    all five missing Shorts were in it and the missing full episode was not),
    and the series feeds have to be fetched anyway to know which series a new
    episode belongs to.

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
    return parse_feed(_get(f"{FEED}?channel_id={channel_id()}"))


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

    Skips anything a re-check has already found GONE from YouTube: that video
    is deliberately absent, and re-adding it every sync is the "local delete
    resurrected by the next pull" trap."""
    from services import youtube_refresh

    uploads = channel_uploads()
    if not uploads:
        return []

    have = {row[0] for row in db.session.query(Teaching.youtube_id).all()}
    gone = youtube_refresh.missing_video_ids()
    candidates = [i for i in uploads if i.youtube_id not in have and i.youtube_id not in gone]
    if not candidates:
        return []

    placing, shorts = series_by_video()
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
