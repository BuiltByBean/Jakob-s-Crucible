"""Canonical Bible book data + scripture reference parsing.

Pure logic, no Flask/DB imports — used by the seed pipeline, the Explore
Scripture pages, and search (a query like "Haggai 2" resolves to a book/chapter).

Parsing targets the notation used in the channel's video descriptions:
    "Haggai 1 v. 1-2:9"        -> Haggai 1:1 - 2:9
    "Psalm 19 v. 7-11"         -> Psalm 19:7-11
    "Psalm c. 90 v. 2, c. 139" -> Psalm 90:2 and Psalm 139
    "Isaiah 60"                -> whole chapter
    "PSALM 110 Part 3"         -> Psalm 110 (trailing noise ignored)
as well as plain "John 3:16" / "1 John 2:3-6" forms.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (name, slug, testament, chapters, [alternate names/abbreviations])
# Order = canonical Protestant order; 1-based book numbers match list position + 1.
BOOKS: list[tuple[str, str, str, int, list[str]]] = [
    ("Genesis", "genesis", "OT", 50, ["gen", "ge", "gn"]),
    ("Exodus", "exodus", "OT", 40, ["exod", "exo", "ex"]),
    ("Leviticus", "leviticus", "OT", 27, ["lev", "le", "lv"]),
    ("Numbers", "numbers", "OT", 36, ["num", "nu", "nm", "nb"]),
    ("Deuteronomy", "deuteronomy", "OT", 34, ["deut", "deu", "dt"]),
    ("Joshua", "joshua", "OT", 24, ["josh", "jos", "jsh"]),
    ("Judges", "judges", "OT", 21, ["judg", "jdg", "jg", "jdgs"]),
    ("Ruth", "ruth", "OT", 4, ["rth", "ru"]),
    ("1 Samuel", "1-samuel", "OT", 31, ["1 sam", "1sam", "1 sa", "i samuel", "first samuel"]),
    ("2 Samuel", "2-samuel", "OT", 24, ["2 sam", "2sam", "2 sa", "ii samuel", "second samuel"]),
    ("1 Kings", "1-kings", "OT", 22, ["1 kgs", "1kgs", "1 ki", "i kings", "first kings"]),
    ("2 Kings", "2-kings", "OT", 25, ["2 kgs", "2kgs", "2 ki", "ii kings", "second kings"]),
    ("1 Chronicles", "1-chronicles", "OT", 29, ["1 chr", "1chr", "1 chron", "i chronicles"]),
    ("2 Chronicles", "2-chronicles", "OT", 36, ["2 chr", "2chr", "2 chron", "ii chronicles"]),
    ("Ezra", "ezra", "OT", 10, ["ezr"]),
    ("Nehemiah", "nehemiah", "OT", 13, ["neh", "ne"]),
    ("Esther", "esther", "OT", 10, ["est", "esth", "es"]),
    ("Job", "job", "OT", 42, ["jb"]),
    ("Psalms", "psalms", "OT", 150, ["psalm", "ps", "psa", "pss", "psm"]),
    ("Proverbs", "proverbs", "OT", 31, ["prov", "pro", "prv", "pr"]),
    ("Ecclesiastes", "ecclesiastes", "OT", 12, ["eccl", "ecc", "ec", "qoheleth"]),
    ("Song of Solomon", "song-of-solomon", "OT", 8, ["song of songs", "song", "sos", "canticles", "cant"]),
    ("Isaiah", "isaiah", "OT", 66, ["isa", "is"]),
    ("Jeremiah", "jeremiah", "OT", 52, ["jer", "je", "jr"]),
    ("Lamentations", "lamentations", "OT", 5, ["lam", "la"]),
    ("Ezekiel", "ezekiel", "OT", 48, ["ezek", "eze", "ezk"]),
    ("Daniel", "daniel", "OT", 12, ["dan", "da", "dn"]),
    ("Hosea", "hosea", "OT", 14, ["hos", "ho"]),
    ("Joel", "joel", "OT", 3, ["jl"]),
    ("Amos", "amos", "OT", 9, ["am"]),
    ("Obadiah", "obadiah", "OT", 1, ["obad", "ob"]),
    ("Jonah", "jonah", "OT", 4, ["jon", "jnh"]),
    ("Micah", "micah", "OT", 7, ["mic", "mc"]),
    ("Nahum", "nahum", "OT", 3, ["nah", "na"]),
    ("Habakkuk", "habakkuk", "OT", 3, ["hab", "hb"]),
    ("Zephaniah", "zephaniah", "OT", 3, ["zeph", "zep", "zp"]),
    ("Haggai", "haggai", "OT", 2, ["hag", "hg"]),
    ("Zechariah", "zechariah", "OT", 14, ["zech", "zec", "zc"]),
    ("Malachi", "malachi", "OT", 4, ["mal", "ml"]),
    ("Matthew", "matthew", "NT", 28, ["matt", "mat", "mt"]),
    ("Mark", "mark", "NT", 16, ["mrk", "mk", "mr"]),
    ("Luke", "luke", "NT", 24, ["luk", "lk"]),
    ("John", "john", "NT", 21, ["jhn", "jn"]),
    ("Acts", "acts", "NT", 28, ["act", "ac"]),
    ("Romans", "romans", "NT", 16, ["rom", "ro", "rm"]),
    ("1 Corinthians", "1-corinthians", "NT", 16, ["1 cor", "1cor", "1 co", "i corinthians"]),
    ("2 Corinthians", "2-corinthians", "NT", 13, ["2 cor", "2cor", "2 co", "ii corinthians"]),
    ("Galatians", "galatians", "NT", 6, ["gal", "ga"]),
    ("Ephesians", "ephesians", "NT", 6, ["eph", "ephes"]),
    ("Philippians", "philippians", "NT", 4, ["phil", "php", "philip"]),
    ("Colossians", "colossians", "NT", 4, ["col", "co"]),
    ("1 Thessalonians", "1-thessalonians", "NT", 5, ["1 thess", "1thess", "1 th", "i thessalonians"]),
    ("2 Thessalonians", "2-thessalonians", "NT", 3, ["2 thess", "2thess", "2 th", "ii thessalonians"]),
    ("1 Timothy", "1-timothy", "NT", 6, ["1 tim", "1tim", "1 ti", "i timothy"]),
    ("2 Timothy", "2-timothy", "NT", 4, ["2 tim", "2tim", "2 ti", "ii timothy"]),
    ("Titus", "titus", "NT", 3, ["tit", "ti"]),
    ("Philemon", "philemon", "NT", 1, ["phlm", "philem", "phm"]),
    ("Hebrews", "hebrews", "NT", 13, ["heb", "he"]),
    ("James", "james", "NT", 5, ["jas", "jm", "jms"]),
    ("1 Peter", "1-peter", "NT", 5, ["1 pet", "1pet", "1 pe", "i peter"]),
    ("2 Peter", "2-peter", "NT", 3, ["2 pet", "2pet", "2 pe", "ii peter"]),
    ("1 John", "1-john", "NT", 5, ["1 jn", "1jn", "1 jo", "i john"]),
    ("2 John", "2-john", "NT", 1, ["2 jn", "2jn", "2 jo", "ii john"]),
    ("3 John", "3-john", "NT", 1, ["3 jn", "3jn", "3 jo", "iii john"]),
    ("Jude", "jude", "NT", 1, ["jud", "jde"]),
    ("Revelation", "revelation", "NT", 22, ["rev", "re", "apocalypse", "revelations"]),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower().replace(".", ""))


# name/alias (normalised) -> 1-based book number. Longer aliases are matched
# first by the parser so "1 john" can never resolve as "john".
_LOOKUP: dict[str, int] = {}
for _i, (_name, _slug, _t, _ch, _aliases) in enumerate(BOOKS, start=1):
    _LOOKUP[_norm(_name)] = _i
    for _a in _aliases:
        _LOOKUP[_norm(_a)] = _i
_ALIASES_BY_LENGTH = sorted(_LOOKUP, key=len, reverse=True)


@dataclass
class ParsedRef:
    """One parsed scripture reference span."""

    book_number: int  # 1-66
    chapter_start: int | None = None
    verse_start: int | None = None
    chapter_end: int | None = None
    verse_end: int | None = None
    display: str = ""

    @property
    def book_name(self) -> str:
        return BOOKS[self.book_number - 1][0]


def book_by_slug(slug: str) -> tuple[int, str, str, int] | None:
    """Return (book_number, name, testament, chapters) for a URL slug."""
    for i, (name, s, testament, chapters, _aliases) in enumerate(BOOKS, start=1):
        if s == slug:
            return (i, name, testament, chapters)
    return None


def find_book(text: str) -> int | None:
    """Resolve a whole string to a book number, if it names exactly one book."""
    return _LOOKUP.get(_norm(text))


# One chapter/verse chunk after a book name. Handles:
#   "1:1-2:9"  "1 v. 1-2:9"  "19 v. 7-11"  "c. 90 v. 2"  "110"  "3:16"
# The 'cdot' group captures an explicit 'c.' chapter marker; 'vs' a verse via
# ':' or 'v.'. Bare numbers are resolved contextually in parse_references —
# after a verse-bearing chunk, "28 v. 15, 22, 38-40" reads 22 and 38-40 as
# more verses of chapter 28 (the descriptions write 'c.' for a new chapter).
_CHUNK = re.compile(
    r"""(?P<cdot>c\.\s*)?(?P<ch>\d{1,3})        # chapter (or bare number)
        (?:\s*(?::|v\.?)\s*(?P<vs>\d{1,3})      # :verse or v. verse
            (?:\s*[-–—]\s*            # range dash
                (?:(?P<ch2>\d{1,3})\s*:\s*)?    # optional end chapter
                (?P<ve>\d{1,3})
            )?
        )?""",
    re.VERBOSE,
)

# Bare verse item inside a comma list that already established a chapter:
# '22' or '38-40' in 'Deuteronomy 28 v. 15, 22, 30, 38-40'.
_BARE_VERSE = re.compile(r"(?P<vs>\d{1,3})(?:\s*[-–—]\s*(?P<ve>\d{1,3}))?")

_BOOK_RE = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in _ALIASES_BY_LENGTH) + r")\b[\s.]*",
    re.IGNORECASE,
)


def parse_references(text: str) -> list[ParsedRef]:
    """Extract scripture references from free text.

    Conservative by design: a book name with no usable chapter yields a
    book-level ref only when the text is short (a title), never from prose —
    otherwise every mention of 'John' in a paragraph becomes a reference.
    """
    if not text:
        return []
    refs: list[ParsedRef] = []
    normalised = text.replace(".", ". ")  # "v.7" -> "v. 7" so \b matching holds

    for m in _BOOK_RE.finditer(normalised):
        book_num = _LOOKUP.get(_norm(m.group(1)))
        if not book_num:
            continue
        # Guard: bare-word aliases like "is"/"am"/"me" only count when followed
        # by a digit or c. chunk. Full names always count.
        alias_norm = _norm(m.group(1))
        full_name = _norm(BOOKS[book_num - 1][0])
        tail = normalised[m.end():]
        has_chunk = bool(re.match(r"(?:c\.\s*)?\d", tail))
        if not has_chunk:
            if alias_norm == full_name and len(text) <= 120:
                refs.append(ParsedRef(book_num, display=BOOKS[book_num - 1][0]))
            continue

        # Consume comma-separated chunks: "19 v. 7-11", "c. 90 v. 2, c. 139",
        # "28 v. 15, 22, 38-40" (bare items after a verse = more verses of the
        # same chapter; the descriptions write 'c.' when a new chapter starts).
        pos = 0
        max_ch = BOOKS[book_num - 1][3]
        current_chapter: int | None = None
        verse_context = False
        while True:
            chunk = _CHUNK.match(tail, pos)
            if not chunk:
                break
            has_verse_marker = chunk.group("vs") is not None
            explicit_chapter = chunk.group("cdot") is not None
            if verse_context and not has_verse_marker and not explicit_chapter and current_chapter:
                # Bare number in an established verse list -> verse(s) of the
                # current chapter: '22' or '38-40'.
                bare = _BARE_VERSE.match(tail, pos)
                end = bare.end()
                ref = ParsedRef(
                    book_number=book_num,
                    chapter_start=current_chapter,
                    verse_start=int(bare.group("vs")),
                    chapter_end=current_chapter if bare.group("ve") else None,
                    verse_end=int(bare.group("ve")) if bare.group("ve") else None,
                )
                ref.display = format_ref(ref)
                refs.append(ref)
            else:
                end = chunk.end()
                ch = int(chunk.group("ch"))
                if 1 <= ch <= max_ch:
                    vs = chunk.group("vs")
                    ve = chunk.group("ve")
                    ch2 = chunk.group("ch2")
                    ref = ParsedRef(
                        book_number=book_num,
                        chapter_start=ch,
                        verse_start=int(vs) if vs else None,
                        chapter_end=int(ch2) if ch2 else (ch if vs and ve else None),
                        verse_end=int(ve) if ve else None,
                    )
                    ref.display = format_ref(ref)
                    refs.append(ref)
                    current_chapter = ch
                    verse_context = has_verse_marker
            # Continue after ', ' only if the next chunk is numeric/c., and the
            # number is not itself the start of a numbered book — in
            # "Colossians 2 v. 8, 2 Timothy 3 v. 16" the second '2' belongs to
            # Timothy, and "Isaiah 60, Revelation 21" must not read 21 as
            # Isaiah's. A continuation number is followed by ':', ',', a dash,
            # 'v.', 'c.', or the end — never by a bare word.
            nxt = re.match(
                r"\s*,\s*(?=(?:c\.\s*)?\d{1,3}\s*(?:[:,\-–—]|v\.|c\.|$|\s*$))",
                tail[end:],
            )
            if not nxt:
                break
            pos = end + nxt.end()

    # Dedupe identical spans, preserve order.
    seen: set[tuple] = set()
    out = []
    for r in refs:
        key = (r.book_number, r.chapter_start, r.verse_start, r.chapter_end, r.verse_end)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def format_ref(ref: ParsedRef) -> str:
    """Canonical human display: 'Haggai 1:1–2:9', 'Psalm 90:2', 'Psalms'."""
    name = ref.book_name
    if name == "Psalms" and ref.chapter_start:
        name = "Psalm"
    if not ref.chapter_start:
        return name
    s = f"{name} {ref.chapter_start}"
    if ref.verse_start:
        s += f":{ref.verse_start}"
        if ref.chapter_end and ref.chapter_end != ref.chapter_start:
            s += f"–{ref.chapter_end}:{ref.verse_end}" if ref.verse_end else f"–{ref.chapter_end}"
        elif ref.verse_end and ref.verse_end != ref.verse_start:
            s += f"–{ref.verse_end}"
    elif ref.chapter_end and ref.chapter_end != ref.chapter_start:
        s += f"–{ref.chapter_end}"
    return s
