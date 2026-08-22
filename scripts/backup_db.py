"""Point-in-time backup of the SQLite database.

Why this exists: the DB lives on the Railway volume and holds work that exists
nowhere else — Jakob's manuscripts, the statement of faith, every page edit,
the admin passwords, and the contact-message inbox. A volume protects against
a redeploy; it does NOT protect against a bad write, a mistaken delete, or a
corrupted file. This does.

VACUUM INTO takes a consistent snapshot of a live database (readers and
writers keep working, WAL is folded in, and the copy is compacted) — unlike
`cp`, which can capture a torn file mid-transaction.

    python scripts/backup_db.py                 # snapshot into DATA_DIR/backups
    railway ssh "python scripts/backup_db.py"   # production

Copy one down with:  railway ssh "cat /data/backups/<name>" > local.db
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

KEEP = 14  # rolling snapshots; at ~3MB each this is trivial against a 50GB volume


def run(keep: int = KEEP) -> int:
    import sqlite3

    from config import DATA_DIR

    db_path = Path(DATA_DIR) / "wisdom_crucible.db"
    if not db_path.is_file():
        print(f"No database at {db_path} — nothing to back up.")
        return 1

    backup_dir = Path(DATA_DIR) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"wisdom_crucible-{stamp}.db"

    conn = sqlite3.connect(str(db_path))
    try:
        # VACUUM INTO refuses to overwrite, so the timestamp must be unique.
        conn.execute("VACUUM INTO ?", (str(target),))
    finally:
        conn.close()

    # Sanity-check the copy before trusting it, then prune the oldest.
    check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        teachings = check.execute("SELECT count(*) FROM teachings").fetchone()[0]
        messages = check.execute("SELECT count(*) FROM contact_messages").fetchone()[0]
        scripts = check.execute(
            "SELECT count(*) FROM teachings WHERE manuscript IS NOT NULL AND manuscript != ''"
        ).fetchone()[0]
    finally:
        check.close()

    print(f"BACKUP {target.name}  ({target.stat().st_size // 1024}KB): "
          f"{teachings} teachings, {scripts} manuscripts, {messages} messages")

    snapshots = sorted(backup_dir.glob("wisdom_crucible-*.db"))
    for old in snapshots[:-keep]:
        old.unlink()
        print(f"  pruned {old.name}")
    print(f"{len(sorted(backup_dir.glob('wisdom_crucible-*.db')))} snapshot(s) kept in {backup_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
