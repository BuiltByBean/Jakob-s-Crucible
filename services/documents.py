"""Manuscript and study-notes documents.

Two jobs:
  * convert Jakob's Word manuscripts to the markdown the site renders
    (the same conversion scripts/docx_to_manuscripts.py uses — one
    implementation, so a web upload and a batch import can never diverge);
  * store uploaded study-notes files somewhere that survives a deploy.

Storage rule: uploads go to DATA_DIR/notes (the mounted volume), NEVER to
static/. Anything under static/ is baked into the container image and is
destroyed on the next deploy — and would additionally be served from the
site's own origin with a Content-Type taken from its extension. Stored names
are always <youtube_id><ext>, derived from the database, never from the
uploaded filename.
"""
from __future__ import annotations

import re
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A short line that is nothing but bold — Jakob's section-header idiom.
_BOLD_HEADER = re.compile(r"^\*{2,3}([^*]{1,70})\*{2,3}$")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
NOTES_EXTENSIONS = (".pdf", ".docx")
MANUSCRIPT_EXTENSIONS = (".docx", ".md", ".txt")

# File signatures: the extension is a claim, these are evidence.
_MAGIC = {
    ".pdf": b"%PDF-",
    ".docx": b"PK\x03\x04",
}


def notes_dir() -> Path:
    from config import DATA_DIR

    path = Path(DATA_DIR) / "notes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _runs_markdown(paragraph) -> str:
    """All runs in document order, hyperlink children included, with
    bold/italic merged across adjacent same-format runs. Soft breaks (w:br)
    become newlines — Jakob writes poetic line breaks for speaking cadence."""
    pieces: list[tuple[str, bool, bool]] = []
    for r in paragraph._p.iter(f"{W}r"):
        rpr = r.find(f"{W}rPr")

        def _on(tag, rpr=rpr):
            if rpr is None:
                return False
            el = rpr.find(f"{W}{tag}")
            return el is not None and el.get(f"{W}val") not in ("false", "0", "none")

        text = ""
        for child in r:
            if child.tag == f"{W}t":
                text += child.text or ""
            elif child.tag == f"{W}br":
                text += "\n"
            elif child.tag == f"{W}tab":
                text += " "
        if text:
            pieces.append((text, _on("b"), _on("i")))

    merged: list[tuple[str, bool, bool]] = []
    for text, b, i in pieces:
        if merged and merged[-1][1] == b and merged[-1][2] == i:
            merged[-1] = (merged[-1][0] + text, b, i)
        else:
            merged.append((text, b, i))

    out = ""
    for text, b, i in merged:
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        core = text.strip()
        if not core:
            out += text
            continue
        if b and i:
            core = f"***{core}***"
        elif b:
            core = f"**{core}**"
        elif i:
            core = f"*{core}*"
        out += lead + core + trail
    return out.strip()


def docx_to_markdown(source) -> str:
    """Convert a .docx (path or file-like) to the site's markdown subset."""
    import docx

    doc = docx.Document(source)
    lines: list[str] = []
    for p in doc.paragraphs:
        style = (p.style.name if p.style is not None else "") or ""
        text = _runs_markdown(p)
        if not text:
            continue
        m = re.match(r"Heading (\d)", style)
        if style == "Title" or (m and m.group(1) == "1"):
            lines.append(f"## {text}")
        elif m:
            lines.append(f"### {text}")
        elif "List" in style:
            lines.append(f"- {text}")
        elif _BOLD_HEADER.match(text):
            lines.append(f"### {_BOLD_HEADER.match(text).group(1).strip()}")
        else:
            lines.append(text)
        lines.append("")

    # Keep consecutive bullets in one block (no blank line between items).
    squeezed: list[str] = []
    for line in lines:
        if line == "" and squeezed and squeezed[-1].startswith("- "):
            continue
        if squeezed and squeezed[-1].startswith("- ") and line and not line.startswith("- "):
            squeezed.append("")
        squeezed.append(line)
    md = "\n".join(squeezed)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def extension_of(filename: str, allowed: tuple[str, ...]) -> str | None:
    ext = Path(filename or "").suffix.lower()
    return ext if ext in allowed else None


def magic_ok(head: bytes, ext: str) -> bool:
    """Does the file's first bytes match what its extension claims?"""
    expected = _MAGIC.get(ext)
    return True if expected is None else head.startswith(expected)


def read_manuscript_upload(storage) -> tuple[str, str | None]:
    """(markdown, error). Accepts .docx, .md, .txt from a Werkzeug FileStorage."""
    ext = extension_of(storage.filename, MANUSCRIPT_EXTENSIONS)
    if ext is None:
        return "", "Please choose a .docx, .md, or .txt file."

    data = storage.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return "", "That file is larger than 25MB."
    if not data:
        return "", "That file was empty."
    if not magic_ok(data, ext):
        return "", f"That doesn't look like a real {ext} file."

    if ext == ".docx":
        import io

        try:
            return docx_to_markdown(io.BytesIO(data)), None
        except Exception as exc:  # noqa: BLE001 — a corrupt upload must not 500
            return "", f"Could not read that Word file ({type(exc).__name__})."
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return "", "Could not read that file as text — please save it as UTF-8."


def save_notes_upload(teaching, storage) -> str | None:
    """Store a study-notes file on the volume. Returns an error, or None."""
    ext = extension_of(storage.filename, NOTES_EXTENSIONS)
    if ext is None:
        return "Study notes must be a .pdf or .docx file."

    data = storage.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return "That file is larger than 25MB."
    if not data:
        return "That file was empty."
    if not magic_ok(data, ext):
        return f"That doesn't look like a real {ext} file."

    directory = notes_dir()
    # One notes file per teaching: clear the other extension so a PDF replacing
    # a DOCX can't leave both behind for notes_path to pick between.
    for other in NOTES_EXTENSIONS:
        stale = directory / f"{teaching.youtube_id}{other}"
        if stale.is_file():
            stale.unlink()
    (directory / f"{teaching.youtube_id}{ext}").write_bytes(data)
    return None


def delete_notes(teaching) -> bool:
    """Remove the uploaded notes file (committed launch files are untouched)."""
    removed = False
    directory = notes_dir()
    for ext in NOTES_EXTENSIONS:
        path = directory / f"{teaching.youtube_id}{ext}"
        if path.is_file():
            path.unlink()
            removed = True
    return removed
