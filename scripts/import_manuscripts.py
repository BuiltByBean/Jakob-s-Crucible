"""Import manuscripts (blog-form scripts) from content/manuscripts/<slug>.md.

The owner writes scripts as spoken essays; dropping one in as markdown named
after the teaching's slug publishes it on the episode page. Survives re-seeds
(seed_db captures and restores manuscripts).

    python scripts/import_manuscripts.py            # skip anything edited in /admin
    python scripts/import_manuscripts.py --force    # repo files win regardless

Since the admin can edit manuscripts, this file-based import is a SECOND
source of truth. By default it refuses to overwrite a manuscript the owner has
edited through the admin (recorded in admin_edits) — otherwise a routine run
of this script after a sync would silently discard his rewrite.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MANUSCRIPT_DIR = REPO / "content" / "manuscripts"


def run(force: bool = False) -> None:
    from app import create_app
    from models import AdminEdit, Teaching, db
    from services import search as search_svc

    app = create_app()
    MANUSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(MANUSCRIPT_DIR.glob("*.md"))
    if not files:
        print(f"No manuscripts found in {MANUSCRIPT_DIR} — name files <teaching-slug>.md")
        return

    with app.app_context():
        # youtube_ids whose manuscript the owner has edited in the admin
        edited = set()
        if not force:
            import json as _json

            for row in AdminEdit.query.filter_by(entity_type="teaching").all():
                try:
                    if "manuscript" in _json.loads(row.payload or "{}"):
                        edited.add(row.entity_key)
                except (ValueError, TypeError):
                    continue

        imported = protected = 0
        for path in files:
            teaching = Teaching.query.filter_by(slug=path.stem).first()
            if teaching is None:
                print(f"  SKIP {path.name}: no teaching with slug {path.stem!r}")
                continue
            if teaching.youtube_id in edited:
                protected += 1
                print(f"  KEEP {path.name}: edited in the admin — use --force to overwrite")
                continue
            teaching.manuscript = path.read_text(encoding="utf-8")
            imported += 1
            print(f"  OK   {path.name} -> {teaching.title}")
        db.session.commit()
        if imported:
            search_svc.rebuild_index()
        print(f"Imported {imported} manuscript(s)"
              + (f", kept {protected} admin-edited" if protected else ""))


if __name__ == "__main__":
    run(force="--force" in sys.argv)
