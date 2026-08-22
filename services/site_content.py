"""Admin-editable site copy.

One registry, one table. Templates call `content('scripture.description')`;
the value is the SiteContent row if the owner has edited it, otherwise the
DEFAULT declared here — which is the exact wording the site shipped with. So
the site renders identically until something is actually edited, deleting a
row restores the shipped copy, and a key that loses its editor still renders.

Kinds drive the admin form widget AND the render path:
  text      one line, rendered as plain text
  multiline short prose, newlines become <br>
  rich      prose with **bold**, *italic* and [links](/path)
  markdown  long-form (headings, quotes, lists) — the manuscript renderer
  url/email/phone  validated single values
  links     an ordered list of {name, url} rows
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from flask import g

from models import SiteContent, db

# --- entry -----------------------------------------------------------------


class Entry:
    __slots__ = ("key", "group", "label", "help", "kind", "default", "rows")

    def __init__(self, key, group, label, kind, default, help="", rows=4):
        self.key = key
        self.group = group
        self.label = label
        self.kind = kind
        self.default = default
        self.help = help
        self.rows = rows


# --- groups (one admin screen each) ----------------------------------------

GROUPS = [
    ("home", "Home page", "The hero line and the wayfinding cards."),
    ("library", "Teaching Library", "The blurb under the Teaching Library heading."),
    ("scripture", "Explore Scripture", "The description and the shelf legend."),
    ("topics", "Explore Topics", "The description above the topic cards."),
    ("statement_of_faith", "Statement of Faith", "The page description and the statement itself."),
    ("resources", "Study Resources", "The blurb above the resource list."),
    ("about", "About", "The ministry description and your biography."),
    ("contact", "Contact", "The blurb above the contact form."),
    ("ministry", "Contact details & links", "Email, phone, and every link to your channels."),
]

GROUP_LABELS = {slug: label for slug, label, _ in GROUPS}


# --- the registry ----------------------------------------------------------

REGISTRY: list[Entry] = [
    # ---- home ----
    Entry("home.hero_subline", "home", "Hero line (under the tagline)", "text",
          "Verse-by-verse study and thoughtful reflection with Jakob McClain"),
    Entry("home.nav_scripture", "home", "“Explore Scripture” card", "multiline",
          "Navigate the library according to testament, book, and chapter."),
    Entry("home.nav_topics", "home", "“Explore Topics” card", "multiline",
          "Navigate the library according to primary themes — apologetics, discipleship, doctrine, and more."),
    Entry("home.nav_search", "home", "“Search” card", "multiline",
          "Filter by Scripture, topic, or even specific phrases within individual teachings."),
    Entry("home.youtube_handle", "home", "YouTube address shown on the card", "text",
          "youtube.com/@TheWisdomCrucible",
          help="Display text only — the link itself is under Contact details & links."),
    Entry("home.youtube_blurb", "home", "YouTube card blurb", "multiline",
          "Full teachings, shorts, and community posts."),
    Entry("home.x_handle", "home", "X handle shown on the card", "text", "@WisdomCrucible"),
    Entry("home.start_intro", "home", "“Start with these” — intro episode", "multiline",
          "Meet Jakob and hear what the crucible is for."),
    Entry("home.start_statement", "home", "“Start with these” — Statement of Faith", "multiline",
          "The doctrinal foundation, laid out at length."),
    Entry("home.start_featured", "home", "“Start with these” — featured teaching", "multiline",
          "The flagship study — what faith actually is."),

    # ---- library ----
    Entry("library.description", "library", "Description", "multiline",
          "Every episode in one place — watch, listen, read, or download from each teaching's page."),

    # ---- scripture ----
    Entry("scripture.description", "scripture", "Description", "rich",
          "Peruse the shelves. Books the crucible has opened are displayed with a "
          "glow representing the enlightenment of the Word of God. The badge counts "
          "the teachings that expound them. The rest of the canon stands ready, "
          "waiting its turn to be opened within the crucible.", rows=5),
    Entry("scripture.legend_gold", "scripture", "Legend — gold books", "text",
          "gold highlight — fully expounded verse-by-verse"),
    Entry("scripture.legend_lit", "scripture", "Legend — glowing books", "text",
          "blue highlight — partially expounded within a teaching"),
    Entry("scripture.legend_unlit", "scripture", "Legend — dim books", "text",
          "not yet opened"),

    # ---- topics ----
    Entry("topics.description", "topics", "Description", "rich",
          "A deliberately small set of themes to guide your way to particular categories "
          "of content. Not all content fits a category, and some content fits multiple "
          "categories. If you don't find what you're looking for, try the [Search](/search) "
          "feature or submit a request on the [Contact](/contact) page.", rows=5,
          help="Square-bracket links work: [Search](/search)."),

    # ---- statement of faith ----
    Entry("statement_of_faith.description", "statement_of_faith", "Page description", "rich",
          "The primary doctrinal truths this ministry holds firmly, stated plainly — "
          "each conviction with the Scriptures that carry it. Weigh everything "
          "explained here against not only the proof texts, but also the cohesive "
          "narrative of all Scripture.", rows=5),
    Entry("statement_of_faith.body", "statement_of_faith", "The statement", "markdown", "", rows=30,
          help="Start a section with ### — e.g. “### The Trinity”. Blank line between paragraphs. "
               "**bold**, *italic*, > quotations, and - bullet lists all work."),

    # ---- resources ----
    Entry("resources.description", "resources", "Description", "rich",
          "These are tools potentially worth using for your own study of Scripture. The "
          "list is deliberately short. Resources are only added after they have "
          "demonstrated their merit in the Crucible.", rows=4),

    # ---- about ----
    # NOTE: one line per paragraph, blank line between. A single newline is a
    # hard line break in the renderer, so source-code wrapping would show as
    # forced mid-sentence breaks on the public page.
    Entry("about.ministry_body", "about", "About the ministry", "markdown",
          "The Wisdom Crucible exists to refine understanding through Scripture — where study leads to transformation."
          "\n\n"
          "The crucible symbolizes this mortal life in which we are continually refined by trials — the heat that purifies and burns away what does not belong. The blue-white flame is not merely for aesthetic preference, but because a blue flame burns hotter than the typical red-to-yellow flame. It reflects the desire for a passion that burns intensely for the understanding and application of the truth revealed in the Holy Scriptures of the Bible."
          "\n\n"
          "The mission is to help others grow in curiosity, understanding, and — God willing — a deepening passion for both the Word of God and for God Himself. That mission is pursued through two complementary forms of teaching — detailed and methodical verse-by-verse and topical studies that draw truth directly from Scripture, and reflective messages that connect biblical principles to everyday life through vivid illustration and thoughtful meditation.",
          rows=14),
    Entry("about.bio_body", "about", "About Jakob", "markdown",
          "My name is Jakob McClain, and my primary roles are those of husband, father, physical therapist, and Bible study teacher — yet, more than those things, my identity is in Christ! I was raised in Northeast Texas, educated in the Baylor Doctor of Physical Therapy program, and most importantly, trained from childhood as a lifelong student of Scripture in preparation to obey the Great Commission — to do my part for the sake of discipling all people."
          "\n\n"
          "Until autumn of 2025, my focus when studying Scripture was mostly to develop my personal relationship with God. In fact, I had expressly told God that I did not want to teach because I didn’t think that role would suit me. It seems the problem with that idea was that God had prepared me to suit that role. He eventually employed several others of the body of Christ to ask me to consider serving as a teacher for a Sunday morning adult Bible study. I eventually agreed, and I’ve never regretted that obedient response for a single moment!"
          "\n\n"
          "Ministry isn’t a hobby. It’s a mission. The Wisdom Crucible is my attempt to dedicate my mission to the Lord — to be a disciple of Jesus Christ laboring to produce disciples of Jesus Christ!",
          rows=14),
    Entry("about.begin_statement", "about", "“Where to begin” — Statement of Faith card", "multiline",
          "The doctrinal foundation, stated plainly — the right place to weigh this ministry's teaching."),
    Entry("about.begin_library", "about", "“Where to begin” — Library card", "multiline",
          "Every study and reflection, organised by series — watch, listen, or read from one page."),

    # ---- contact ----
    Entry("contact.description", "contact", "Description", "rich",
          "Questions, challenges, encouragement — all welcome! Challenge the theology "
          "if you want; just be sure to bring Scripture with you!", rows=4),

    # ---- ministry identity ----
    Entry("ministry.tagline", "ministry", "Tagline", "text",
          "Where wisdom distills transformation through the Word of God.",
          help="Shown under the crucible on the home page and in search-engine results."),
    Entry("ministry.email", "ministry", "Public email address", "email", "thewisdomcrucible@gmail.com",
          help="Shown in the footer and on the Contact page."),
    Entry("ministry.contact_recipient", "ministry", "Send contact-form messages to", "email",
          "thewisdomcrucible@gmail.com",
          help="Where the contact form delivers. Messages are always saved in Messages regardless."),
    Entry("ministry.phone", "ministry", "Phone number", "phone", "254-242-1486",
          help="Digits and dashes, e.g. 254-242-1486."),
    Entry("ministry.youtube_url", "ministry", "YouTube channel", "url",
          "https://www.youtube.com/@TheWisdomCrucible"),
    Entry("ministry.youtube_community_url", "ministry", "YouTube community page", "url",
          "https://www.youtube.com/@TheWisdomCrucible/community"),
    Entry("ministry.podcast_url", "ministry", "Podcast (Spotify)", "url",
          "https://open.spotify.com/show/5FKIF6sWeLLaXOhHG6k9BL",
          help="Every “Listen” button on the site points here. Spotify is the podcast's home."),
    Entry("ministry.x_url", "ministry", "X (Twitter)", "url", "https://x.com/WisdomCrucible"),
    Entry("ministry.podcast_platforms", "ministry", "Podcast platforms", "links",
          json.dumps([
              {"name": "Spotify", "url": "https://open.spotify.com/show/5FKIF6sWeLLaXOhHG6k9BL"},
              {"name": "iHeart Radio", "url": "https://www.iheart.com/podcast/269-the-wisdom-crucible-with-j-302846080"},
              {"name": "Amazon Music", "url": "https://music.amazon.com/podcasts/04784b2a-2059-4aaa-9a12-5c4ddbb0482c/the-wisdom-crucible-with-jakob-mcclain"},
              {"name": "Apple Podcasts", "url": "https://podcasts.apple.com/us/podcast/the-wisdom-crucible-with-jakob-mcclain/id1848749337"},
          ]),
          help="Listed on the home page. Add, rename, or remove platforms as you go."),
]

BY_KEY = {e.key: e for e in REGISTRY}


def entries_for(group: str) -> list[Entry]:
    return [e for e in REGISTRY if e.group == group]


# --- read ------------------------------------------------------------------

def _overrides() -> dict[str, str]:
    """All override rows, loaded once per request."""
    if "site_content" not in g:
        try:
            g.site_content = {row.key: row.value for row in SiteContent.query.all()}
        except Exception as exc:  # noqa: BLE001 — a missing table must never 500 the site
            db.session.rollback()
            logging.warning("site_content unavailable (%s) — using shipped defaults", exc)
            g.site_content = {}
    return g.site_content


def content(key: str, default: str = "") -> str:
    """The current value for a key: the owner's edit, else the shipped text."""
    value = _overrides().get(key)
    if value is not None and value.strip() != "":
        return value
    entry = BY_KEY.get(key)
    return entry.default if entry else default


def _parse_links(raw: str) -> list[dict]:
    try:
        rows = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("name") and safe_url(row.get("url", "")):
            out.append({"name": str(row["name"])[:80], "url": safe_url(row["url"])})
    return out


def links(key: str) -> list[dict]:
    """A 'links' value as [{name, url}], dropping anything malformed.

    An override that parses to an EMPTY list falls back to the shipped
    default. A stored "[]" is almost always damage rather than intent (a form
    posted before its rows rendered), and silently blanking every podcast link
    on the home page is much worse than ignoring one odd row."""
    rows = _parse_links(content(key))
    if rows:
        return rows
    entry = BY_KEY.get(key)
    return _parse_links(entry.default) if entry else []


def is_overridden(key: str) -> bool:
    return bool((_overrides().get(key) or "").strip())


# --- write -----------------------------------------------------------------

MAX_VALUE_LENGTH = 200_000


def save(key: str, value: str, editor: str = "") -> None:
    """Upsert one key. An empty value deletes the override, restoring the
    shipped default (that is the owner's 'undo')."""
    value = (value or "").replace("\r\n", "\n").strip()[:MAX_VALUE_LENGTH]
    row = SiteContent.query.filter_by(key=key).first()
    if not value:
        if row is not None:
            db.session.delete(row)
        g.pop("site_content", None)
        return
    if row is None:
        row = SiteContent(key=key)
        db.session.add(row)
    row.value = value
    row.updated_by = (editor or "")[:320]
    g.pop("site_content", None)


# --- validation ------------------------------------------------------------

ALLOWED_SCHEMES = ("http", "https", "mailto", "tel")


def safe_url(value: str) -> str:
    """Return the URL if it is safe to put in an href, else ''.

    Blocks javascript:/data: and anything with control characters — an admin
    field that reaches an href is the site's real stored-XSS surface."""
    value = (value or "").strip()
    if not value or any(ord(c) < 32 for c in value):
        return ""
    if value.startswith("/") and not value.startswith("//"):
        return value[:400]  # internal path
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme not in ALLOWED_SCHEMES:
        return ""
    if parts.scheme in ("http", "https") and not parts.netloc:
        return ""
    return value[:400]


def validation_error(entry: Entry, value: str) -> str | None:
    """Human-readable problem with a submitted value, or None."""
    value = (value or "").strip()
    if not value:
        return None  # empty = restore the default
    if entry.kind == "url" and not safe_url(value):
        return "Please enter a full web address starting with https://"
    if entry.kind == "email":
        if " " in value or value.count("@") != 1 or "." not in value.split("@")[-1]:
            return "Please enter a valid email address."
    if entry.kind == "phone":
        digits = [c for c in value if c.isdigit()]
        if not 7 <= len(digits) <= 15:
            return "Please enter a valid phone number."
    if entry.kind == "links":
        try:
            rows = json.loads(value)
        except (ValueError, TypeError):
            return "Could not read those links."
        for row in rows if isinstance(rows, list) else []:
            if row.get("name") and not safe_url(row.get("url", "")):
                return f"“{row['name']}” needs a full web address starting with https://"
    if len(value) > MAX_VALUE_LENGTH:
        return "That's too long to store."
    return None


# --- first-run seeding -----------------------------------------------------

def seed_statement_of_faith_if_empty() -> None:
    """Move the shipped statement into the DB once, so the owner can edit it.

    content/statement_of_faith.md is a repo file: on Railway it is rebuilt from
    git on every deploy, so an edit written back to it would be silently lost.
    The DB (on the volume) is the only durable home."""
    import re

    from config import BASE_DIR

    key = "statement_of_faith.body"
    marker = "statement_of_faith.seeded"
    try:
        # The marker, not the row, decides. Checking only for the row meant a
        # deliberate "clear this field" (the documented undo) was silently
        # re-populated from the repo file on the next boot or redeploy.
        if SiteContent.query.filter_by(key=marker).first() is not None:
            return
        if SiteContent.query.filter_by(key=key).first() is not None:
            db.session.add(SiteContent(key=marker, value="1", updated_by="seed"))
            db.session.commit()
            return
        path = BASE_DIR / "content" / "statement_of_faith.md"
        if not path.is_file():
            return
        text = re.sub(r"^### Personal Statement of Faith\s*\n", "", path.read_text(encoding="utf-8"))
        db.session.add(SiteContent(key=key, value=text.strip(), updated_by="seed"))
        db.session.add(SiteContent(key=marker, value="1", updated_by="seed"))
        db.session.commit()
        logging.info("site_content: seeded statement of faith (%d chars)", len(text))
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logging.warning("site_content: could not seed statement of faith (%s)", exc)
