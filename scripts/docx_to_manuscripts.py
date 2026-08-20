"""Convert Jakob's NOTES .docx files into content/manuscripts/<slug>.md.

Walks each paragraph's XML (including w:hyperlink children, which
paragraph.text silently drops), maps heading styles to markdown headings,
list paragraphs to bullets/numbers, and bold/italic runs to **/*. Output
targets the site's deliberately small manuscript renderer — plain markdown,
no tables, no images. Requires python-docx (dev-only, like the sync deps).

Usage: python scripts/docx_to_manuscripts.py <docx_dir>
The DOCX_MAP below names which file belongs to which teaching slug.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "content" / "manuscripts"

# docx filename -> teaching slug (see data/seed; slugs are stable).
DOCX_MAP = {
    "theology_of_faith_NOTES.docx": "what-is-faith",
    "fundamentals_of_the_faith_NOTES.docx": "what-is-fundamental-for-the-faith",
    "QITC_#0_NOTES.docx": "how-to-handle-objections-to-scripture",
    "QITC_#1_NOTES.docx": "does-the-bible-teach-divine-ignorance",
    "Haggai_P1_NOTES.docx": "the-house-of-god-and-the-heart-of-man",
    "Haggai_P2_NOTES.docx": "reverse-of-the-curse-the-signet-ring-returns",
    "Psalm_110_P1_NOTES.docx": "what-about-the-superscript",
    "Psalm_110_P2_NOTES.docx": "who-s-who",
    "Psalm_110_P3_NOTES.docx": "the-order-of-melchizedek",
    "i_am_Darth_Vader_NOTES.docx": "i-am-darth-vader",
    "armor_identifies_soldiers_NOTES.docx": "equipping-the-armor",
    "ripples_and_responsibilities_NOTES.docx": "ripple-analogy-properly-prioritizing-relationships",
    "introducing_TWC_NOTES.docx": "introducing-the-wisdom-crucible-with-jakob-mcclain",
    # statement_of_faith.docx is handled separately -> content/statement_of_faith.md
}

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _runs_markdown(paragraph) -> str:
    """All runs in document order, hyperlink children included, with
    bold/italic markers merged across adjacent same-format runs. Soft line
    breaks (w:br) become newlines — Jakob scripts with poetic line breaks for
    speaking cadence, and the site renderer shows them as <br>."""
    pieces: list[tuple[str, bool, bool]] = []
    for r in paragraph._p.iter(f"{W}r"):
        rpr = r.find(f"{W}rPr")

        def _on(tag):
            if rpr is None:
                return False
            el = rpr.find(f"{W}{tag}")
            return el is not None and el.get(f"{W}val") not in ("false", "0", "none")

        # children in document order: text, soft breaks, tabs
        text = ""
        for child in r:
            tag = child.tag
            if tag == f"{W}t":
                text += child.text or ""
            elif tag == f"{W}br":
                text += "\n"
            elif tag == f"{W}tab":
                text += " "
        if not text:
            continue
        pieces.append((text, _on("b"), _on("i")))

    merged: list[tuple[str, bool, bool]] = []
    for text, b, i in pieces:
        if merged and merged[-1][1] == b and merged[-1][2] == i:
            merged[-1] = (merged[-1][0] + text, b, i)
        else:
            merged.append((text, b, i))

    out = ""
    for text, b, i in merged:
        # markers can't wrap leading/trailing whitespace
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


# A short line that is nothing but bold (Jakob's header idiom in these docs)
_BOLD_HEADER = re.compile(r"^\*{2,3}([^*]{1,70})\*{2,3}$")


def convert(path: Path) -> str:
    import docx

    doc = docx.Document(str(path))
    lines: list[str] = []
    for p in doc.paragraphs:
        style = (p.style.name if p.style is not None else "") or ""
        text = _runs_markdown(p)
        if not text:
            continue
        m = re.match(r"Heading (\d)", style)
        if style == "Title" or (m and m.group(1) == "1"):
            lines.append(f"## {text}")
        elif m and m.group(1) == "2":
            lines.append(f"### {text}")
        elif m:
            lines.append(f"### {text}")
        elif "List" in style:
            lines.append(f"- {text}")
        elif _BOLD_HEADER.match(text):
            # lone bold line = a section header in Jakob's authoring style
            lines.append(f"### {_BOLD_HEADER.match(text).group(1).strip()}")
        else:
            lines.append(text)
        lines.append("")
    # keep consecutive bullets in one block (no blank line between items)
    squeezed: list[str] = []
    for line in lines:
        if line == "" and len(squeezed) >= 1 and squeezed[-1].startswith("- "):
            continue
        if squeezed and squeezed[-1].startswith("- ") and line and not line.startswith("- "):
            squeezed.append("")
        squeezed.append(line)
    md = "\n".join(squeezed)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    return md


def run(src_dir: Path) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, slug in DOCX_MAP.items():
        src = src_dir / filename
        if not src.is_file():
            print(f"  MISSING {filename}")
            continue
        md = convert(src)
        (OUT_DIR / f"{slug}.md").write_text(md, encoding="utf-8")
        print(f"  {filename} -> {slug}.md ({len(md) // 1000}KB)")

    sof = src_dir / "statement_of_faith.docx"
    if sof.is_file():
        (REPO / "content").mkdir(exist_ok=True)
        md = convert(sof)
        (REPO / "content" / "statement_of_faith.md").write_text(md, encoding="utf-8")
        print(f"  statement_of_faith.docx -> content/statement_of_faith.md ({len(md) // 1000}KB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/docx_to_manuscripts.py <docx_dir>")
    run(Path(sys.argv[1]))
