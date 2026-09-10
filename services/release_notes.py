"""What changed, shown to the maintainer once.

DEVLOG.md is already the user-facing change history every commit appends to
(house rule), so it is the source here rather than a second list somebody has
to remember to keep. It ships inside the image — the Dockerfile's `COPY . .`
takes it and .dockerignore does not exclude it — so a deploy carries its own
release notes, and the notes appear when the work actually goes live rather
than when it is merely pushed.

Each maintainer stores the ID of the newest entry they have acknowledged;
everything ABOVE that entry in the file is what they have not seen. Ordering
is by POSITION in the file, never by date: the log already carries four
entries dated 2026-08-22, so a date is not a total order.

Nothing here fails hard. A missing DEVLOG, a heading that breaks the
convention, or a marker naming an entry that no longer exists all end in
"show nothing" or "show the newest one", never an exception into an admin
page that was working a moment ago.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from config import BASE_DIR

# "## 2026-09-09 — Scripture references read as chips, not a list"
# The dash is matched loosely (em, en, or hyphen) so a heading typed on a
# keyboard without an em dash still becomes an entry instead of vanishing.
ENTRY_RE = re.compile(
    r"^##[ \t]+(\d{4}-\d{2}-\d{2})[ \t]*[—–-][ \t]*(.+?)[ \t]*$",
    re.MULTILINE,
)

# How many entries a single dialog will show. A maintainer coming back after
# months should get the recent work, not a wall of archaeology.
MAX_SHOWN = 6


@dataclass(frozen=True)
class Entry:
    """One dated entry from DEVLOG.md."""

    id: str
    date: str
    title: str
    body: str


def _entry_id(date: str, title: str) -> str:
    """A stable ID for one entry: its date plus a slug of its title.

    Two entries can share a date, so the title has to be part of it. Editing a
    shipped entry's wording changes its ID, which at worst shows that entry
    once more — the safe direction to fail in."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{date}-{slug}"[:160]


def _unwrap(body: str) -> str:
    """Reflow DEVLOG's hard-wrapped prose.

    The log is wrapped at ~78 columns so it reads in a diff, and the renderer
    turns a single newline into a <br> — which is right for a manuscript, where
    the line breaks are the author's, but wrong here: it broke every sentence
    mid-clause at whatever column the file happened to wrap. Joining the lines
    inside each paragraph lets the prose reflow to the dialog's width instead.

    Blank lines still separate paragraphs, and a block that carries its own
    line structure (a list, a heading, a quote) is left exactly as written."""
    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", body):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        structured = any(line.startswith(("-", "*", "#", ">", "1.")) for line in lines)
        blocks.append("\n".join(lines) if structured else " ".join(lines))
    return "\n\n".join(blocks)


@lru_cache(maxsize=1)
def entries() -> tuple[Entry, ...]:
    """Every DEVLOG entry, newest first. Cached: the file only changes when a
    new container is built, and this is read on every admin request."""
    try:
        raw = (BASE_DIR / "DEVLOG.md").read_text(encoding="utf-8")
    except OSError:
        return ()
    found = list(ENTRY_RE.finditer(raw))
    out: list[Entry] = []
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(raw)
        date, title = match.group(1), match.group(2).strip()
        out.append(Entry(
            id=_entry_id(date, title),
            date=date,
            title=title,
            body=_unwrap(raw[match.end():end].strip()),
        ))
    return tuple(out)


def newest_id() -> str:
    """The ID to store when a maintainer acknowledges the dialog."""
    found = entries()
    return found[0].id if found else ""


def unseen(last_seen_id: str) -> list[Entry]:
    """The entries to show someone whose marker is `last_seen_id`.

    A maintainer with no marker at all sees only the newest entry: the first
    sign-in after this shipped should read as "here is what just changed", not
    as the whole history of the site dumped into a dialog. The same applies
    when the marker names an entry that is no longer in the file.
    """
    found = entries()
    if not found:
        return []
    if last_seen_id:
        for index, entry in enumerate(found):
            if entry.id == last_seen_id:
                return list(found[:index][:MAX_SHOWN])
    return [found[0]]
