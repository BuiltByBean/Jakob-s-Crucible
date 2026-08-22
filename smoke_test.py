"""Smoke test: seed a throwaway SQLite DB and walk every page + search path.

Gates every commit. DATABASE_URL is set BEFORE importing app — Config reads
env at class-definition (import) time; setting it after import would silently
run against the real dev database. Run: python smoke_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Throwaway DB, wired up BEFORE any project import.
_tmp = tempfile.mkdtemp(prefix="twc-smoke-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'smoke.db'}"
os.environ["DATA_DIR"] = _tmp

sys.path.insert(0, str(Path(__file__).resolve().parent))

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL  {label} {detail}")


def run() -> int:
    from scripts.seed_db import run as seed

    seed()

    from app import create_app
    from models import ScriptureBook, Series, Teaching, Topic

    app = create_app()
    client = app.test_client()

    with app.app_context():
        teachings = Teaching.query.all()
        series = Series.query.all()
        topics = Topic.query.all()
        books_with_slug = [b.slug for b in ScriptureBook.query.limit(3)]
        teaching_slugs = [t.slug for t in teachings]
        series_slugs = [s.slug for s in series]
        topic_slugs = [t.slug for t in topics]
        n_teachings = len([t for t in teachings if t.kind == "teaching"])
        n_shorts = len([t for t in teachings if t.kind == "short"])
        sof = Teaching.query.filter_by(is_statement_of_faith=True).count()

    print("\n-- seed sanity")
    check("has full teachings", n_teachings >= 10, f"got {n_teachings}")
    check("has shorts", n_shorts >= 10, f"got {n_shorts}")
    # 5 = Verse-by-Verse, Topical, Reflection & Application, QITC, Shorts
    # (owner split 'Detailed & Verse-by-Verse' into two playlists 2026-08).
    check("has 5 series", len(series) == 5, f"got {len(series)}")
    check("statement of faith flagged once", sof == 1, f"got {sof}")

    print("\n-- static pages")
    for path in ("/", "/about", "/teachings", "/teachings?show=shorts", "/teachings?show=all",
                 "/scripture", "/topics", "/search", "/search?q=faith", "/search?q=Haggai+2",
                 "/resources", "/contact", "/statement-of-faith", "/healthz"):
        r = client.get(path, follow_redirects=True)
        check(f"GET {path}", r.status_code == 200, f"-> {r.status_code}")

    print("\n-- every teaching page")
    for slug in teaching_slugs:
        r = client.get(f"/teachings/{slug}")
        check(f"GET /teachings/{slug}", r.status_code == 200, f"-> {r.status_code}")

    print("\n-- every series page")
    for slug in series_slugs:
        r = client.get(f"/series/{slug}")
        check(f"GET /series/{slug}", r.status_code == 200, f"-> {r.status_code}")
        r = client.get(f"/teachings?series={slug}")
        check(f"GET /teachings?series={slug}", r.status_code == 200, f"-> {r.status_code}")

    print("\n-- every topic page")
    for slug in topic_slugs:
        r = client.get(f"/topics/{slug}")
        check(f"GET /topics/{slug}", r.status_code == 200, f"-> {r.status_code}")

    print("\n-- scripture book pages (sample + every book with content)")
    from models import ScriptureRef, db as _db

    with app.app_context():
        with_content = [b.slug for b in ScriptureBook.query.join(
            ScriptureRef, ScriptureRef.book_id == ScriptureBook.id).distinct().all()]
    for slug in set(books_with_slug + with_content):
        r = client.get(f"/scripture/{slug}")
        check(f"GET /scripture/{slug}", r.status_code == 200, f"-> {r.status_code}")
    r = client.get("/scripture/haggai?chapter=2")
    check("GET /scripture/haggai?chapter=2", r.status_code == 200, f"-> {r.status_code}")

    print("\n-- search finds transcript hits with timestamps")
    # 'consider your ways' is spoken in both Haggai teachings (verified in the
    # caption corpus) — 'signet' is NOT: auto-captions transcribe it away, so
    # a title word makes a vacuous transcript-search probe.
    r = client.get("/search?q=consider+your+ways")
    check("transcript search 200", r.status_code == 200, f"-> {r.status_code}")
    # data-video-start only appears on transcript-hit buttons — a real,
    # non-vacuous marker that timestamped spoken-word results rendered.
    check("transcript search shows timestamped hits", b"data-video-start" in r.data and b"<mark>" in r.data)

    print("\n-- notes download route")
    from config import BASE_DIR as _base

    with app.app_context():
        with_notes = Teaching.query.filter(Teaching.notes_url != "").first()
        notes_slug = with_notes.slug if with_notes else None
    if notes_slug:
        local = any((_base / "static" / "notes" / f"{notes_slug}{e}").is_file() for e in (".pdf", ".docx"))
        r = client.get(f"/notes/{notes_slug}")
        if local:
            check("notes route serves self-hosted file",
                  r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition", ""),
                  f"-> {r.status_code}")
        else:
            check("notes route redirects to external notes", r.status_code == 302, f"-> {r.status_code}")
    r = client.get("/notes/not-a-real-slug")
    check("notes route 404s on unknown slug", r.status_code == 404, f"-> {r.status_code}")

    print("\n-- 404s")
    for path in ("/teachings/not-a-real-slug", "/series/nope", "/topics/nope", "/scripture/nope"):
        r = client.get(path)
        check(f"GET {path} -> 404", r.status_code == 404, f"-> {r.status_code}")

    print("\n-- contact form")
    r = client.get("/contact")
    token = None
    for line in r.data.decode().splitlines():
        if 'name="csrf_token"' in line:
            import re as _re

            m = _re.search(r'value="([^"]+)"', line)
            token = m.group(1) if m else None
            break
    check("csrf token present", token is not None)
    r = client.post("/contact", data={
        "csrf_token": token or "", "name": "Smoke Test", "email": "smoke@example.com",
        "subject": "Hello", "message": "A test message.", "website": "",
    }, follow_redirects=True)
    check("contact POST succeeds", r.status_code == 200 and b"has been sent" in r.data, f"-> {r.status_code}")
    r = client.post("/contact", data={
        "csrf_token": token or "", "name": "Bot", "email": "bot@example.com",
        "subject": "spam", "message": "spam", "website": "http://spam.example",
    }, follow_redirects=True)
    check("honeypot silently succeeds", r.status_code == 200, f"-> {r.status_code}")
    with app.app_context():
        from models import ContactMessage

        stored = ContactMessage.query.count()
    check("real message stored, bot message not", stored == 1, f"got {stored}")

    # ---- admin -------------------------------------------------------------
    # These assert the NEGATIVE cases: for admin routes an unauthenticated 200
    # is the bug, which a route-returns-200 sweep would happily miss.
    print("\n-- admin is locked")
    import re as _re

    with app.app_context():
        from models import AdminUser, db as _db
        from services.auth import hash_password

        _db.session.add(AdminUser(email="smoke@example.com", name="Smoke",
                                  password_hash=hash_password("smoke-password-1"),
                                  is_active=True, session_epoch=1))
        _db.session.commit()

    # Every admin rule is guarded: GET redirects to the login, POST is refused
    # outright (never bounce a POST to a form — the body is already gone).
    admin_rules = [r for r in app.url_map.iter_rules() if str(r.rule).startswith("/admin")]
    check("admin routes exist", len(admin_rules) >= 10, f"got {len(admin_rules)}")
    anon = app.test_client()
    unguarded = []
    for rule in admin_rules:
        if rule.endpoint == "admin.login":
            continue
        path = str(rule.rule)
        if "<" in path:  # concrete value for parameterised rules
            path = _re.sub(r"<[^>]+>", "1", path)
        if "GET" in rule.methods:
            resp = anon.get(path)
            if resp.status_code not in (302, 303, 403, 404):
                unguarded.append(f"GET {path} -> {resp.status_code}")
        if "POST" in rule.methods:
            resp = anon.post(path, data={})
            if resp.status_code not in (403, 400, 404):
                unguarded.append(f"POST {path} -> {resp.status_code}")
    check("every admin route refuses anonymous access", not unguarded, "; ".join(unguarded[:4]))

    r = anon.get("/admin/", follow_redirects=True)
    check("anonymous admin lands on the login form", b'name="password"' in r.data)

    def _token(html: bytes) -> str:
        m = _re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
        return m.group(1).decode() if m else ""

    r = anon.get("/admin/login")
    check("login page has a CSRF token", bool(_token(r.data)))
    r = anon.post("/admin/login", data={"email": "smoke@example.com", "password": "smoke-password-1"})
    check("login without CSRF is refused", r.status_code == 400, f"-> {r.status_code}")

    r = anon.post("/admin/login", data={
        "csrf_token": _token(anon.get("/admin/login").data),
        "email": "smoke@example.com", "password": "wrong",
    })
    check("wrong password is rejected", r.status_code == 401, f"-> {r.status_code}")

    r = anon.post("/admin/login", data={
        "csrf_token": _token(anon.get("/admin/login").data),
        "email": "smoke@example.com", "password": "smoke-password-1",
    })
    check("correct password signs in", r.status_code == 302, f"-> {r.status_code}")
    r = anon.get("/admin/", follow_redirects=True)
    check("dashboard renders when signed in", b"Overview" in r.data or b"Welcome" in r.data)
    check("admin responses are noindex", "noindex" in anon.get("/admin/").headers.get("X-Robots-Tag", ""))

    # An open redirect through ?next= would make the ministry domain a phishing hop.
    from services.auth import safe_next

    check("safe_next rejects off-site targets",
          all(safe_next(bad) is None for bad in
              ("//evil.example", "https://evil.example", "/\\evil.example", "/teachings", "\\\\evil")))
    check("safe_next allows admin paths", safe_next("/admin/resources") == "/admin/resources")

    r = anon.post("/admin/logout", data={"csrf_token": _token(anon.get("/admin/").data)})
    check("logout signs out", r.status_code == 302, f"-> {r.status_code}")
    r = anon.get("/admin/", follow_redirects=True)
    check("dashboard is locked again after logout", b'name="password"' in r.data)

    print("\n-- robots")
    r = client.get("/robots.txt")
    check("robots.txt disallows /admin", r.status_code == 200 and b"Disallow: /admin" in r.data)

    print()
    if FAILURES:
        print(f"SMOKE TEST FAILED — {len(FAILURES)} failure(s)")
        return 1
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run())
