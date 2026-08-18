"""Import manuscripts (blog-form scripts) from content/manuscripts/<slug>.md.

The owner writes scripts as spoken essays; dropping one in as markdown named
after the teaching's slug publishes it on the episode page. Survives re-seeds
(seed_db captures and restores manuscripts). Run: python scripts/import_manuscripts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MANUSCRIPT_DIR = REPO / "content" / "manuscripts"


def run() -> None:
    from app import create_app
    from models import Teaching, db
    from services import search as search_svc

    app = create_app()
    MANUSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(MANUSCRIPT_DIR.glob("*.md"))
    if not files:
        print(f"No manuscripts found in {MANUSCRIPT_DIR} — name files <teaching-slug>.md")
        return

    with app.app_context():
        imported = 0
        for path in files:
            teaching = Teaching.query.filter_by(slug=path.stem).first()
            if teaching is None:
                print(f"  SKIP {path.name}: no teaching with slug {path.stem!r}")
                continue
            teaching.manuscript = path.read_text(encoding="utf-8")
            imported += 1
            print(f"  OK   {path.name} -> {teaching.title}")
        db.session.commit()
        if imported:
            search_svc.rebuild_index()
        print(f"Imported {imported} manuscript(s)")


if __name__ == "__main__":
    run()
