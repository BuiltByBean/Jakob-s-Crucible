"""Durability for admin edits to content that the YouTube sync rebuilds.

scripts/seed_db.py deletes and recreates series, topics, resources and
teachings on every content sync. Anything the owner edits by hand in those
tables would vanish with them. So every admin edit is ALSO written here, as a
row keyed by slug / youtube_id (never row id — ids change on every rebuild),
and replayed onto the fresh rows at the end of the re-seed.

The content tables are derived data; this table is the record of intent.
"""
from __future__ import annotations

import json
import logging

from models import AdminEdit, Resource, Series, Teaching, Topic, db

SERIES = "series"
TOPIC = "topic"
RESOURCES = "resources"
TEACHING = "teaching"


def record(entity_type: str, entity_key: str, payload: dict, editor: str = "") -> None:
    """Upsert the intent record for one entity (merging into any existing
    payload, so editing a topic's name doesn't drop its saved assignments)."""
    row = AdminEdit.query.filter_by(entity_type=entity_type, entity_key=entity_key).first()
    if row is None:
        row = AdminEdit(entity_type=entity_type, entity_key=entity_key, payload="{}")
        db.session.add(row)
    try:
        merged = json.loads(row.payload or "{}")
    except (ValueError, TypeError):
        merged = {}
    merged.update(payload)
    row.payload = json.dumps(merged)
    row.updated_by = (editor or "")[:320]


def forget(entity_type: str, entity_key: str) -> None:
    AdminEdit.query.filter_by(entity_type=entity_type, entity_key=entity_key).delete()


def _payloads(entity_type: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in AdminEdit.query.filter_by(entity_type=entity_type).all():
        try:
            out[row.entity_key] = json.loads(row.payload or "{}")
        except (ValueError, TypeError):
            continue
    return out


def apply_all() -> dict[str, int]:
    """Replay every recorded edit onto the current content rows.

    Idempotent — safe to call at the end of a re-seed, from a script, or twice
    in a row. Returns counts for the log line."""
    counts = {"series": 0, "topics": 0, "resources": 0, "teachings": 0}

    # ---- series descriptions ----
    for slug, payload in _payloads(SERIES).items():
        series = Series.query.filter_by(slug=slug).first()
        if series is None:
            continue
        if "description" in payload:
            series.description = payload["description"]
            counts["series"] += 1

    # ---- topics: text and (optionally) their teaching assignments ----
    for slug, payload in _payloads(TOPIC).items():
        topic = Topic.query.filter_by(slug=slug).first()
        # A deletion is an intent too: without this record, seed_db's TOPIC_MAP
        # recreates a topic the owner removed — fully populated — on the next
        # channel sync.
        if payload.get("deleted"):
            if topic is not None:
                topic.teachings = []
                db.session.delete(topic)
                counts["topics"] += 1
            continue
        if topic is None and payload.get("name"):
            topic = Topic(slug=slug, name=payload["name"])
            db.session.add(topic)
        if topic is None:
            continue
        for field in ("name", "description", "sort_order"):
            if field in payload:
                setattr(topic, field, payload[field])
        if "youtube_ids" in payload:
            wanted = payload["youtube_ids"] or []
            rows = Teaching.query.filter(Teaching.youtube_id.in_(wanted)).all() if wanted else []
            # Shorts never carry topics (owner's rule) — enforced here too, so
            # a stale record can't reintroduce one.
            topic.teachings = [t for t in rows if t.kind != "short"]
        counts["topics"] += 1

    # ---- resources: the admin owns the whole collection once he edits it ----
    resources_payload = _payloads(RESOURCES).get("*")
    if resources_payload and isinstance(resources_payload.get("items"), list):
        Resource.query.delete()
        db.session.flush()
        for i, item in enumerate(resources_payload["items"], start=1):
            db.session.add(Resource(
                category=(item.get("category") or "Resources")[:120],
                category_order=int(item.get("category_order") or 1),
                name=(item.get("name") or "")[:200],
                url=(item.get("url") or "")[:400],
                description=item.get("description") or "",
                sort_order=int(item.get("sort_order") or i),
            ))
            counts["resources"] += 1

    # ---- per-teaching flags ----
    featured_ids = []
    for youtube_id, payload in _payloads(TEACHING).items():
        teaching = Teaching.query.filter_by(youtube_id=youtube_id).first()
        if teaching is None:
            continue
        if "is_featured" in payload:
            if payload["is_featured"]:
                featured_ids.append(teaching.id)
            counts["teachings"] += 1
        if "notes_hidden" in payload:
            teaching.notes_hidden = bool(payload["notes_hidden"])
            counts["teachings"] += 1
        # The owner's own writing: restored if a sync rebuilt this teaching
        # without it (a transient yt-dlp error drops a video from the scrape).
        if payload.get("manuscript") and not (teaching.manuscript or "").strip():
            teaching.manuscript = payload["manuscript"]
            counts["teachings"] += 1
    if featured_ids:
        # Exactly one featured teaching: the newest recorded pick wins.
        Teaching.query.filter(Teaching.is_featured.is_(True)).update(
            {"is_featured": False}, synchronize_session=False
        )
        Teaching.query.filter(Teaching.id == featured_ids[-1]).update(
            {"is_featured": True}, synchronize_session=False
        )

    db.session.commit()
    if any(counts.values()):
        logging.info(
            "admin edits replayed: %d series, %d topics, %d resources, %d teachings",
            counts["series"], counts["topics"], counts["resources"], counts["teachings"],
        )
    return counts


def snapshot_resources(editor: str = "") -> None:
    """Record the CURRENT resources table as the owner's collection."""
    items = []
    for r in Resource.query.order_by(Resource.category_order, Resource.category, Resource.sort_order).all():
        items.append({
            "category": r.category, "category_order": r.category_order,
            "name": r.name, "url": r.url, "description": r.description,
            "sort_order": r.sort_order,
        })
    record(RESOURCES, "*", {"items": items}, editor)
