"""Statically verify every url_for() target in templates against app.url_map.

url_for() calls inside conditional Jinja branches hide BuildError 500s from
happy-path testing — this check fires on every branch. Exit 1 on any miss.
Run: python scripts/check_url_for_endpoints.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

URL_FOR_RE = re.compile(r"url_for\(\s*['\"]([^'\"]+)['\"]")


def run() -> int:
    from app import app

    registered = {rule.endpoint for rule in app.url_map.iter_rules()}
    failures = []
    for path in sorted((REPO / "templates").rglob("*.html")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for endpoint in URL_FOR_RE.findall(line):
                if endpoint not in registered and endpoint != "static":
                    failures.append(f"{path.relative_to(REPO)}:{n} -> {endpoint!r}")

    if failures:
        print("UNREGISTERED url_for() TARGETS:")
        for f in failures:
            print(" ", f)
        return 1
    print(f"OK: all template url_for() targets resolve ({len(registered)} endpoints registered)")
    return 0


if __name__ == "__main__":
    sys.exit(run())
