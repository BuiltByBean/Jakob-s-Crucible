"""Deleting an episode from the admin, for good.

The owner asked for this to scrub "all evidence of its prior existence" — the
case that prompted it was a podcast-feed duplicate the new-uploads sync had
added as the newest episode. So a delete removes the row and everything that
hangs off it (chapters, Scripture refs, captions, topic links, search index),
the files keyed on its youtube_id on the volume (uploaded notes, refreshed
thumbnail), and every admin record that names it (topic assignments, the
featured pin, the "Start with these" picks).

The ONE thing it keeps is a tombstone: the episode's admin_edits record is
replaced with {"deleted": true}. That is what stops the new-uploads sync and
the full re-seed from recreating the episode — both rebuild from YouTube, and
the video is still there. It carries no content, only the id.
"""
from __future__ import annotations

import json
import logging

from models import AdminEdit, TranscriptSegment, db


def drop_rows(teaching) -> None:
    """Delete the row and its children. No commit, no files, no records —
    shared with admin_edits.apply_all(), which replays a delete on a re-seed."""
    TranscriptSegment.query.filter_by(teaching_id=teaching.id).delete(synchronize_session=False)
    teaching.topics = []
    db.session.delete(teaching)


def _scrub_records(youtube_id: str) -> None:
    from services import admin_edits

    for row in AdminEdit.query.filter_by(entity_type=admin_edits.TOPIC).all():
        try:
            payload = json.loads(row.payload or "{}")
        except (ValueError, TypeError):
            continue
        ids = payload.get("youtube_ids")
        if isinstance(ids, list) and youtube_id in ids:
            payload["youtube_ids"] = [i for i in ids if i != youtube_id]
            row.payload = json.dumps(payload)
    for row in AdminEdit.query.filter_by(entity_type=admin_edits.FEATURED, entity_key="*").all():
        try:
            payload = json.loads(row.payload or "{}")
        except (ValueError, TypeError):
            continue
        if payload.get("youtube_id") == youtube_id:
            payload["youtube_id"] = None  # back to "always the newest"
            row.payload = json.dumps(payload)


def _scrub_start_picks(youtube_id: str, editor: str) -> None:
    from services import site_content as sc

    picks = [v.strip() for v in sc.content("home.start_picks").split(",") if v.strip()]
    if youtube_id in picks:
        sc.save("home.start_picks", ",".join(v for v in picks if v != youtube_id), editor)


def _scrub_files(youtube_id: str) -> None:
    from services import documents, youtube_refresh

    paths = list(documents.notes_dir().glob(f"{youtube_id}.*"))
    paths.append(youtube_refresh.thumbs_dir() / f"{youtube_id}.jpg")
    for path in paths:
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:  # a stray file is not worth failing the delete
            logging.warning("episodes: could not remove %s (%s)", path.name, exc)


def delete(teaching, editor: str = "") -> str:
    """Remove one episode completely. Returns its title for the message."""
    from services import admin_edits, youtube_refresh

    youtube_id, title, slug = teaching.youtube_id, teaching.title, teaching.slug
    drop_rows(teaching)
    _scrub_records(youtube_id)
    _scrub_start_picks(youtube_id, editor)
    youtube_refresh.forget(youtube_id)
    # Replace, not merge: the manuscript, description and closing note in the
    # old record are the "evidence" the owner asked to be rid of.
    admin_edits.forget(admin_edits.TEACHING, youtube_id)
    admin_edits.record(admin_edits.TEACHING, youtube_id, {"deleted": True}, editor)
    db.session.commit()

    _scrub_files(youtube_id)
    youtube_refresh.reindex()
    logging.info("episodes: %s (%s) deleted by %s", slug, youtube_id, editor or "?")
    return title
