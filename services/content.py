"""Parsing of the channel's structured YouTube descriptions.

Jakob writes descriptions with consistent '_____' dividers and labelled
sections (Context, Timestamps, Study Notes & Key Passages, Connections &
Updates, Closing Note). Shared by the seed pipeline and the episode page so
displayed sections can never drift from what seeding extracted.
"""
from __future__ import annotations

import re

DIVIDER_RE = re.compile(r"^_{5,}\s*$", re.MULTILINE)
# A hashtag token must contain a letter — '#1'-style number references in
# prose are not hashtags and must survive.
HASHTAG_RE = re.compile(r"(?:^|(?<=\s))#\w*[A-Za-z]\w*")
NOTES_RE = re.compile(r"Download study notes:\s*(\S+)", re.IGNORECASE)
PRIMARY_RE = re.compile(r"Primary text:\s*(.+)", re.IGNORECASE)
REFERENCE_RE = re.compile(r"Reference Text:\s*(.+)", re.IGNORECASE)
CROSSREF_LINE_RE = re.compile(r"^\s*([A-Z][A-Za-z .]+?)\s+–\s+(.+)$")  # 'Section – refs' lines
TIMESTAMP_RE = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*[-–—]\s*(.+)$")

# Section titles we recognise (lowercased, trailing colon stripped).
_KNOWN_HEADINGS = {
    "context": "context",
    "timestamps": "timestamps",
    "study notes & key passages": "notes",
    "study notes and key passages": "notes",
    "connections & updates": "connections",
    "connections and updates": "connections",
    "closing note": "closing",
}


def strip_hashtags(text: str) -> str:
    """Remove YouTube hashtag tokens (#FaithReflection etc.) — YouTube-specific
    metadata that shouldn't be reproduced on the site (owner's rule). Lines
    that were nothing but hashtags disappear entirely; blank paragraph
    separators are preserved."""
    if not text or "#" not in text:
        return text
    kept: list[str] = []
    for line in text.splitlines():
        stripped = HASHTAG_RE.sub("", line)
        if line.strip() and not stripped.strip():
            continue  # the line held only hashtags
        kept.append(re.sub(r"[ \t]{2,}", " ", stripped).rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def split_description(description: str) -> dict:
    """Split a description into
    {summary, context, timestamps_raw, notes_raw, closing, notes_url}.

    The hook paragraph(s) before the first divider become the summary.
    Unrecognised sections are ignored for display but stay in the full text.
    """
    out = {"summary": "", "context": "", "timestamps_raw": "", "notes_raw": "", "closing": "", "notes_url": ""}
    if not description:
        return out

    blocks = DIVIDER_RE.split(description)
    # Displayed sections (summary/context/closing) shed YouTube hashtags;
    # timestamps/notes are machine-parsed and never carry them.
    out["summary"] = strip_hashtags(blocks[0].strip())

    for block in blocks[1:]:
        block = block.strip()
        if not block:
            continue
        first_line, _, rest = block.partition("\n")
        key = _KNOWN_HEADINGS.get(first_line.strip().rstrip(":").lower())
        if key == "context":
            out["context"] = strip_hashtags(rest.strip())
        elif key == "timestamps":
            out["timestamps_raw"] = rest.strip()
        elif key == "notes":
            out["notes_raw"] = rest.strip()
        elif key == "closing":
            out["closing"] = strip_hashtags(rest.strip())

    m = NOTES_RE.search(description)
    if m:
        out["notes_url"] = m.group(1).strip()
    return out


def extract_scripture_lines(description: str) -> tuple[list[str], list[str]]:
    """Return (primary_lines, reference_lines) of raw scripture-bearing text.

    Primary: 'Primary text: Haggai 1 v. 1-2:9'.
    Reference: 'Reference Text: ...' plus the per-section cross-reference lines
    used by the Statement of Faith episode ('Scripture – Psalm 19 v. 7–11, ...').
    """
    primary: list[str] = []
    reference: list[str] = []
    if not description:
        return primary, reference

    for m in PRIMARY_RE.finditer(description):
        primary.append(m.group(1).strip())
    for m in REFERENCE_RE.finditer(description):
        reference.append(m.group(1).strip())

    sections = split_description(description)
    for line in sections["notes_raw"].splitlines():
        line = line.strip()
        if PRIMARY_RE.search(line) or REFERENCE_RE.search(line):
            continue  # already captured above
        m = CROSSREF_LINE_RE.match(line)
        if m:
            reference.append(m.group(2).strip())
    return primary, reference


def parse_timestamp_lines(raw: str) -> list[tuple[int, str]]:
    """Parse 'H:MM:SS – Title' / 'M:SS – Title' lines to (seconds, title)."""
    out: list[tuple[int, str]] = []
    for line in (raw or "").splitlines():
        m = TIMESTAMP_RE.match(line.strip())
        if not m:
            continue
        h = int(m.group(1) or 0)
        out.append((h * 3600 + int(m.group(2)) * 60 + int(m.group(3)), m.group(4).strip()))
    return out


def slugify(text: str, max_len: int = 80) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s[:max_len].rstrip("-") or "item"


def teaching_slug(title: str) -> str:
    """Slug from the part before '|' (the memorable half of the YT title)."""
    main = title.split("|")[0].strip() or title
    return slugify(main)
