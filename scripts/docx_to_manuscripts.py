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

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# The conversion itself lives in services/documents so the /admin upload and
# this batch import can never diverge.
from services.documents import docx_to_markdown  # noqa: E402

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

def convert(path: Path) -> str:
    return docx_to_markdown(str(path))


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
