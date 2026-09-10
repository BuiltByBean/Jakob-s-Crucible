"""Statement of Faith: the markdown body -> collapsible articles + ref chips.

The statement stays ONE markdown field in the admin (`statement_of_faith.body`)
— structure is derived here, at render time, rather than being a second thing
the owner has to maintain. Two conventions in that markdown carry the whole
page:

    ### The Trinity            a top-level article
    ### A. God the Father      a sub-article of the article above it
    **Scripture References:** Matthew 28:19; 2 Corinthians 13:14.

The semicolons in that line are the DELIMITER, not decoration — they are what
separates one reference from the next, and they are deliberately not rendered
between the resulting chips.

Everything else renders as ordinary prose. Nothing here fails hard on text that
breaks the conventions: an unparseable reference becomes plain text, a
sub-article with no parent is promoted to top level, and a body with no
headings at all renders as a single untitled section.

Parsing on the server (not in the browser, the way the CCC site does it) means
the chips are in the first paint — no flash of unstyled references — and the
whole thing is exercised by smoke_test.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

import bible
from config import BASE_DIR

# --- the ESV bundle --------------------------------------------------------

# static/js/scriptures.js is the single source of truth for which passages have
# text: the browser loads it to render the popup, and we read the same file to
# decide which references are worth making a button. Two copies of that list
# would drift, and the drift would show as a button that opens nothing.
_BUNDLE_PREFIX = "window.TWC_SCRIPTURES="


@lru_cache(maxsize=1)
def bundled_keys() -> frozenset[str]:
    """Passage keys the ESV bundle can render. Empty if the file is missing —
    the page then shows every reference as plain text, which is the same
    graceful degradation as a single unbundled reference."""
    path = BASE_DIR / "static" / "js" / "scriptures.js"
    try:
        raw = path.read_text(encoding="utf-8")
        start = raw.index(_BUNDLE_PREFIX) + len(_BUNDLE_PREFIX)
        return frozenset(json.loads(raw[start:raw.rindex("}") + 1]))
    except (OSError, ValueError):
        return frozenset()


# --- reference parsing -----------------------------------------------------

# "**Scripture References:** Psalm 19:7–11; Mark 7:1–13." — the label is
# matched loosely (Reference/References, with or without the colon) so a small
# edit to the wording doesn't silently turn the chips back into prose.
REFS_LINE = re.compile(r"^\*\*(Scripture\s+References?:?)\*\*[ \t]*(.+)$", re.MULTILINE | re.IGNORECASE)

# "1 Corinthians 12:12-27" -> book, rest. Handles the numbered books and the
# one multi-word book the statement cites (Song of Solomon).
BOOK_RE = re.compile(r"^((?:[123]\s)?[A-Za-z]+(?:\s+of\s+Solomon)?)\s+(\d.*)$")

# "### A. God the Father" / "### B) Communion"
SUB_ARTICLE_RE = re.compile(r"^([A-Z])[.)]\s+(.*)$")


def _norm_key(text: str) -> str:
    """Reference text -> bundle key. The owner types en-dashes (Psalm 19:7–11);
    the bundle keys use plain hyphens."""
    return re.sub(r"\s+", " ", re.sub(r"[–—]", "-", text)).strip()


def _book_slug(name: str) -> str | None:
    number = bible.find_book(name)
    return bible.BOOKS[number - 1][1] if number else None


def parse_refs(line: str, opened_books: frozenset[str] = frozenset()) -> list[dict]:
    """One 'Scripture References:' line -> the pieces to render, in order.

    Each piece is one of:
      {"kind": "chip",  "label", "key", "book_slug", "book_name"}  clickable
      {"kind": "plain", "label"}                                   text only
      {"kind": "sep",   "label"}                                   punctuation

    `opened_books` is the set of book slugs that actually have teachings behind
    them; only those chips carry a link through to Explore Scripture, so the
    popup never offers a door into an empty room.
    """
    have = bundled_keys()
    out: list[dict] = []

    def separate(gap: str) -> None:
        """Separators go in only BETWEEN two references. Emitting one up front
        would leave a stray mark hanging off the start of the line the moment
        the owner typed one semicolon too many.

        The semicolon that splits one reference from the next is NOT rendered:
        the references are chips, and punctuation between two pills is noise.
        It stays in the admin text because it is what the parsing splits on —
        deleting it there is what silently turns the chips back into prose."""
        if out:
            out.append({"kind": "sep", "label": gap})

    for group in line.strip().rstrip(".").split(";"):
        group = group.strip()
        if not group:
            continue
        match = BOOK_RE.match(group)
        if not match:  # not a reference we understand — show it untouched
            separate(" ")
            out.append({"kind": "plain", "label": group})
            continue
        book, spans = match.group(1), match.group(2)
        slug = _book_slug(book)
        for span_index, span in enumerate(spans.split(",")):
            span = span.strip()
            if not span:
                continue
            separate(" " if span_index == 0 else ", ")
            key = _norm_key(f"{book} {span}")
            # The book is repeated only on the first span: "Psalm 19:1-4, 90:2"
            # reads the way it was written.
            label = f"{book} {span}" if span_index == 0 else span
            if key in have:
                out.append({
                    "kind": "chip", "label": label, "key": key,
                    "book_slug": slug if slug in opened_books else None,
                    "book_name": bible.BOOKS[bible.find_book(book) - 1][0] if slug else book,
                })
            else:
                out.append({"kind": "plain", "label": label})
    return out


# --- section parsing -------------------------------------------------------


@dataclass
class Section:
    """One collapsible article of the statement."""

    title: str
    slug: str
    body: str = ""                       # markdown, rendered by the template
    refs: list[dict] = field(default_factory=list)
    refs_label: str = ""                 # the owner's own wording for the label
    children: list["Section"] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.body or self.refs or self.children)


def _slugify(text: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "article"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    taken.add(slug)
    return slug


def _split_body(block: str) -> tuple[str, str, str]:
    """Body markdown, plus the references label and line pulled out of it.

    The label travels with the references so the page keeps whatever wording the
    owner typed — renaming it in the admin renames it on the page."""
    match = REFS_LINE.search(block)
    if not match:
        return block.strip(), "", ""
    rest = (block[:match.start()] + block[match.end():]).strip()
    return rest, match.group(1).strip(), match.group(2).strip()


def parse_sections(markdown: str, opened_books: frozenset[str] = frozenset()) -> list[Section]:
    """The statement's markdown -> top-level articles, each with its children.

    Sub-articles ("### A. God the Father") nest inside the article above them,
    so closing The Trinity closes all three persons with it. A sub-article with
    nothing above it becomes top-level rather than being dropped.
    """
    parts = re.split(r"^###[ \t]*(.+?)[ \t]*$", markdown or "", flags=re.MULTILINE)
    sections: list[Section] = []
    taken: set[str] = set()

    # Anything before the first heading is prose without an article of its own.
    intro = parts[0].strip()
    if intro:
        body, label, refs = _split_body(intro)
        sections.append(Section(title="", slug=_slugify("intro", taken), body=body,
                                refs=parse_refs(refs, opened_books) if refs else [],
                                refs_label=label))

    for title, block in zip(parts[1::2], parts[2::2]):
        body, label, refs = _split_body(block)
        sub = SUB_ARTICLE_RE.match(title)
        section = Section(
            title=title,
            slug=_slugify(title, taken),
            body=body,
            refs=parse_refs(refs, opened_books) if refs else [],
            refs_label=label,
        )
        if sub and sections and sections[-1].title:
            sections[-1].children.append(section)
        else:
            sections.append(section)
    return sections
