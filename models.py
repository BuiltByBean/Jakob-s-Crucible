"""SQLAlchemy models for The Wisdom Crucible.

The Teaching is the central entity: video, podcast, notes, manuscript,
chapters, transcript, scripture references, and topics all hang off it —
one page per episode, never parallel per-medium libraries.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def _utcnow():
    return datetime.now(timezone.utc)


class Series(db.Model):
    """A teaching series — mirrors the channel's YouTube playlists exactly."""

    __tablename__ = "series"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    kind = db.Column(db.String(16), default="teaching")  # teaching | shorts
    sort_order = db.Column(db.Integer, default=0)
    youtube_playlist_id = db.Column(db.String(64), default="")
    podcast_playlist_id = db.Column(db.String(64), default="")
    art_path = db.Column(db.String(255), default="")  # static-relative; falls back to newest episode thumbnail
    created_at = db.Column(db.DateTime, default=_utcnow)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    teachings = db.relationship(
        "Teaching", back_populates="series", order_by="Teaching.sort_order", lazy="selectin"
    )

    @property
    def published_teachings(self):
        return [t for t in self.teachings if t.kind != "short"]


teaching_topics = db.Table(
    "teaching_topics",
    db.Column("teaching_id", db.Integer, db.ForeignKey("teachings.id", ondelete="CASCADE"), primary_key=True),
    db.Column("topic_id", db.Integer, db.ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True),
    # The composite PK only serves teaching_id-prefixed lookups; Topic.teachings
    # filters by topic_id alone and needs its own index (FK house rule).
    db.Index("ix_teaching_topics_topic_id", "topic_id"),
)


class Teaching(db.Model):
    """One episode. kind='short' for YouTube Shorts (available, de-emphasised)."""

    __tablename__ = "teachings"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(160), unique=True, nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    subtitle = db.Column(db.String(300), default="")  # the part after '|' in YT titles
    description = db.Column(db.Text, default="")  # full YT description (structured)
    summary = db.Column(db.Text, default="")  # the hook paragraph before the first divider
    kind = db.Column(db.String(16), default="teaching", index=True)  # teaching | short

    youtube_id = db.Column(db.String(16), unique=True, nullable=False, index=True)
    podcast_youtube_id = db.Column(db.String(16), default="")  # matching podcast-feed episode
    notes_url = db.Column(db.String(400), default="")  # 'Download study notes' link from the description
    thumbnail_url = db.Column(db.String(400), default="")
    duration_seconds = db.Column(db.Integer, default=0)
    published_at = db.Column(db.DateTime, index=True)  # UTC

    manuscript = db.Column(db.Text, default="")  # markdown; blog-form of the script
    is_featured = db.Column(db.Boolean, default=False)
    is_statement_of_faith = db.Column(db.Boolean, default=False)

    series_id = db.Column(
        db.Integer, db.ForeignKey("series.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sort_order = db.Column(db.Integer, default=0)  # position within series

    created_at = db.Column(db.DateTime, default=_utcnow)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    series = db.relationship("Series", back_populates="teachings")
    chapters = db.relationship(
        "Chapter",
        back_populates="teaching",
        order_by="Chapter.start_seconds",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    scripture_refs = db.relationship(
        "ScriptureRef",
        back_populates="teaching",
        order_by="ScriptureRef.id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    transcript_segments = db.relationship(
        "TranscriptSegment",
        back_populates="teaching",
        order_by="TranscriptSegment.start_seconds",
        cascade="all, delete-orphan",
        lazy="dynamic",  # thousands of rows; never load implicitly
    )
    topics = db.relationship("Topic", secondary=teaching_topics, back_populates="teachings")

    @property
    def youtube_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.youtube_id}"

    @property
    def podcast_url(self) -> str:
        if self.podcast_youtube_id:
            return f"https://www.youtube.com/watch?v={self.podcast_youtube_id}"
        return ""

    @property
    def duration_display(self) -> str:
        s = self.duration_seconds or 0
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

    @property
    def primary_refs(self):
        return [r for r in self.scripture_refs if r.is_primary]

    @property
    def has_transcript(self) -> bool:
        return self.transcript_segments.limit(1).count() > 0

    @property
    def has_notes(self) -> bool:
        """Notes exist self-hosted (static/notes/<slug>.pdf) or as an external
        link scraped from the video description."""
        from config import BASE_DIR

        return bool(self.notes_url) or (BASE_DIR / "static" / "notes" / f"{self.slug}.pdf").is_file()


class Chapter(db.Model):
    """Video chapter (timestamp) from the YouTube description."""

    __tablename__ = "chapters"

    id = db.Column(db.Integer, primary_key=True)
    teaching_id = db.Column(
        db.Integer, db.ForeignKey("teachings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_seconds = db.Column(db.Integer, nullable=False, default=0)
    title = db.Column(db.String(300), nullable=False, default="")

    teaching = db.relationship("Teaching", back_populates="chapters")

    @property
    def timestamp_display(self) -> str:
        h, rem = divmod(self.start_seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class TranscriptSegment(db.Model):
    """Timestamped auto-caption segment. The substrate for clip search:
    keeping start/duration per segment is what makes timestamp deep links and
    future natural-language search incremental features instead of rebuilds."""

    __tablename__ = "transcript_segments"

    id = db.Column(db.Integer, primary_key=True)
    teaching_id = db.Column(
        db.Integer, db.ForeignKey("teachings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_seconds = db.Column(db.Float, nullable=False, default=0.0)
    duration_seconds = db.Column(db.Float, default=0.0)
    text = db.Column(db.Text, nullable=False, default="")

    teaching = db.relationship("Teaching", back_populates="transcript_segments")


class ScriptureBook(db.Model):
    """Canonical 66 books, seeded from bible.BOOKS. number = canonical order."""

    __tablename__ = "scripture_books"

    id = db.Column(db.Integer, primary_key=True)  # == canonical book number 1-66
    slug = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name = db.Column(db.String(40), nullable=False)
    testament = db.Column(db.String(2), nullable=False)  # OT | NT
    chapters = db.Column(db.Integer, nullable=False)

    refs = db.relationship("ScriptureRef", back_populates="book", lazy="dynamic")


class ScriptureRef(db.Model):
    """A scripture span a teaching covers or cites.

    is_primary: the passage the teaching expounds ('Primary text:') vs a
    cross-reference. source records provenance so a later admin edit can
    override auto-parsed rows without them resurrecting on re-sync.
    """

    __tablename__ = "scripture_refs"

    id = db.Column(db.Integer, primary_key=True)
    teaching_id = db.Column(
        db.Integer, db.ForeignKey("teachings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id = db.Column(
        db.Integer, db.ForeignKey("scripture_books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_start = db.Column(db.Integer)  # NULL = whole-book reference
    verse_start = db.Column(db.Integer)
    chapter_end = db.Column(db.Integer)
    verse_end = db.Column(db.Integer)
    is_primary = db.Column(db.Boolean, default=False)
    display_text = db.Column(db.String(80), default="")  # e.g. 'Haggai 1:1–2:9'
    source = db.Column(db.String(16), default="auto")  # auto | manual

    teaching = db.relationship("Teaching", back_populates="scripture_refs")
    book = db.relationship("ScriptureBook", back_populates="refs")

    def covers_chapter(self, chapter: int) -> bool:
        if self.chapter_start is None:
            return True
        end = self.chapter_end or self.chapter_start
        return self.chapter_start <= chapter <= end


class Topic(db.Model):
    """Restrained topical taxonomy — a handful of themes, never hundreds of tags."""

    __tablename__ = "topics"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    sort_order = db.Column(db.Integer, default=0)

    teachings = db.relationship("Teaching", secondary=teaching_topics, back_populates="topics")


class Resource(db.Model):
    """Recommended study resource (Study Resources page), grouped by category."""

    __tablename__ = "resources"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(120), nullable=False, index=True)
    category_order = db.Column(db.Integer, default=0)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(400), default="")
    description = db.Column(db.Text, default="")
    sort_order = db.Column(db.Integer, default=0)


class ContactMessage(db.Model):
    """Contact-form submission. Saved BEFORE the email attempt so a mail
    failure can never lose a message."""

    __tablename__ = "contact_messages"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), default="")
    email = db.Column(db.String(320), default="")
    subject = db.Column(db.String(300), default="")
    message = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=_utcnow)
    emailed = db.Column(db.Boolean, default=False)
