"""Single source of truth for column additions to existing tables.

db.create_all() creates missing TABLES but never adds COLUMNS to existing
ones. Every column added to an already-shipped table goes in this list; boot
applies it idempotently. Two drifting copies of this list once broke another
project's production for weeks — keep it to one. Prefer a new side table over
a new column where possible (create_all handles new tables on every dialect).
"""
from __future__ import annotations

import logging

# (table, column, sql_type, default_literal_or_None)
COLUMNS_TO_ADD: list[tuple[str, str, str, str | None]] = [
    # 2026-08 admin inbox: contact messages gained read/archive state.
    ("contact_messages", "read_at", "DATETIME", None),
    # "false" parses on SQLite AND Postgres; a bare 0 is rejected by Postgres for a
    # boolean column, and ensure_columns swallows that failure silently.
    ("contact_messages", "archived", "BOOLEAN", "false"),
    # 2026-08 admin: "notes removed" for a file committed under static/ has to
    # be recorded, since the image is rebuilt from git on every deploy.
    ("teachings", "notes_hidden", "BOOLEAN", "false"),
]


def ensure_columns(db) -> None:
    """Idempotently add any missing columns from COLUMNS_TO_ADD."""
    for table, column, sql_type, default in COLUMNS_TO_ADD:
        ddl = f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
        if default is not None:
            ddl += f" DEFAULT {default}"
        try:
            db.session.execute(db.text(ddl))
            db.session.commit()
            logging.info("schema: added %s.%s", table, column)
        except Exception:  # noqa: BLE001 — already exists (the common case)
            db.session.rollback()


def verify_model_columns(db) -> list[str]:
    """Boot-time drift check: every declared model column must exist in the DB.
    Returns missing 'table.column' names (logged at ERROR by the caller)."""
    missing: list[str] = []
    try:
        inspector = db.inspect(db.engine)
        for table in db.metadata.tables.values():
            if table.name not in inspector.get_table_names():
                missing.append(f"{table.name}.*")
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    missing.append(f"{table.name}.{col.name}")
    except Exception as exc:  # noqa: BLE001
        logging.warning("schema drift check skipped: %s", exc)
    return missing
