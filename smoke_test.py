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

    print("\n-- statement of faith")
    # The page derives its whole structure from two conventions in the owner's
    # markdown (### headings, a **Scripture References:** line). A silent
    # regression there still returns 200, so assert on what it produced.
    html = client.get("/statement-of-faith").get_data(as_text=True)
    from services.site_content import content as site_text
    from services.statement import bundled_keys, parse_sections

    with app.app_context():
        sections = parse_sections(site_text("statement_of_faith.body"))
    subs = sum(len(s.children) for s in sections)
    chips = html.count('class="ref-chip"')
    check("ESV bundle loaded", len(bundled_keys()) > 0, f"got {len(bundled_keys())} passages")
    check("statement splits into articles", len(sections) >= 10, f"got {len(sections)}")
    check("sub-articles nest in their parent", subs >= 5, f"got {subs}")
    check("references render as chips", chips > 50, f"got {chips}")
    check("every chip has a passage", html.count("data-ref=") == chips, "chip without a data-ref")
    check("the ESV overlay is on the page", 'id="scripture-popup"' in html)
    check("the overlay sits outside <main>",
          html.index("</main>") < html.index('id="scripture-popup"'),
          "an overlay inside <main> is inerted along with it")
    check("the passage bundle loads here", "js/scriptures.js" in html)

    print("\n-- birthday greeting")
    # Date-gated on purpose. Two things have to hold: on the day it renders
    # OUTSIDE <main> (an overlay inside it is inerted along with the page),
    # and on every other day of the year it puts not one byte on the page.
    import datetime as _dt

    from services import birthday as bd

    check("MM-DD parses", bd.parse_month_day("08-31") == (8, 31))
    check("a nonsense date is refused", bd.parse_month_day("31-08") is None)
    check("29 February is a real birthday", bd.parse_month_day("02-29") == (2, 29))

    with app.app_context():
        month, day = bd.parse_month_day(site_text("birthday.date"))
    real_today = bd._today
    try:
        bd._today = lambda: _dt.date(2026, month, day)
        on_day = client.get("/").get_data(as_text=True)
        bd._today = lambda: _dt.date(2026, month, day) + _dt.timedelta(days=1)
        off_day = client.get("/").get_data(as_text=True)
    finally:
        bd._today = real_today

    check("the greeting shows on the day", 'id="birthday-greeting"' in on_day)
    check("the fireworks canvas is there", 'id="birthday-fireworks"' in on_day)
    check("the greeting sits outside <main>",
          on_day.index("</main>") < on_day.index('id="birthday-greeting"'),
          "an overlay inside <main> is inerted along with it")
    check("it is gone again the next day", 'id="birthday-greeting"' not in off_day)
    check("its script loads only on the day",
          "js/birthday.js" in on_day and "js/birthday.js" not in off_day)

    print("\n-- related episodes rail")
    # The right-hand rail on an episode page. Three cases, because the wrong
    # one is silent: an episode with topics, one without, and a Short (whose
    # related content is only ever what Jakob links by hand).
    import re as _re2

    with app.app_context():
        from models import Teaching as _T

        _eps = _T.query.filter_by(kind="teaching").all()
        _multi = next((t for t in _eps if len(t.topics) > 1), None)
        _none = next((t for t in _eps if not t.topics), None)
        _short = _T.query.filter_by(kind="short").first()
        _multi_slug = _multi.slug if _multi else ""
        _multi_id = _multi.id if _multi else 0
        _none_slug = _none.slug if _none else ""
        _short_slug = _short.slug if _short else ""

    def _rail(slug):
        html = client.get(f"/teachings/{slug}").get_data(as_text=True)
        return html[html.find("<aside"):html.find("</aside>")]

    if _multi_slug:
        aside = _rail(_multi_slug)
        check("an episode with topics gets the Related episodes rail",
              "Related episodes" in aside)
        check("it is not still called 'In this series'", "In this series" not in aside)
        linked = _re2.findall(r'href="/teachings/([^"]+)"', aside)
        check("it does not link to itself", _multi_slug not in linked)
        check("no episode is listed twice (sharing two topics must not duplicate)",
              len(linked) == len(set(linked)), str(linked))
        with app.app_context():
            from models import Teaching as _T2
            dates = [_T2.query.filter_by(slug=s).first().published_at for s in linked]
            dates = [d for d in dates if d]
            check("newest first", all(a >= b for a, b in zip(dates, dates[1:])),
                  str([str(d)[:10] for d in dates]))
            check("only full episodes are listed",
                  all(_T2.query.filter_by(slug=s).first().kind == "teaching" for s in linked))

    if _none_slug:
        aside = _rail(_none_slug)
        check("an untagged episode shows no rail rather than an empty one",
              "Related episodes" not in aside and "In this series" not in aside)

    if _short_slug:
        aside = _rail(_short_slug)
        check("a Short keeps its series rail", "In this series" in aside)
        check("a Short gets no topic-generated related rail",
              "Related episodes" not in aside,
              "the standing rule is that a Short's related content is hand-linked only")

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

    print("\n-- what's new dialog")
    # The point of this feature is that it appears exactly once. So assert both
    # halves: it is there on the first signed-in page, and it is GONE after the
    # acknowledgement — a dialog that reappears is worse than none at all.
    from services import release_notes as _rn

    _all = _rn.entries()
    check("DEVLOG parses into entries", len(_all) >= 5, f"got {len(_all)}")
    check("a maintainer with no marker sees only the newest",
          [e.id for e in _rn.unseen("")] == [_all[0].id])
    check("an acknowledged marker leaves nothing to show", _rn.unseen(_all[0].id) == [])
    check("a marker further down shows everything above it",
          [e.id for e in _rn.unseen(_all[2].id)] == [_all[0].id, _all[1].id])
    check("a marker that no longer exists does not dump the archive",
          [e.id for e in _rn.unseen("2019-01-01-gone")] == [_all[0].id])
    check("the dialog is capped", len(_rn.unseen(_all[-1].id)) <= _rn.MAX_SHOWN,
          f"got {len(_rn.unseen(_all[-1].id))}")

    r = anon.get("/admin/")
    check("the dialog shows on the first signed-in page", b'whats-new-title' in r.data)
    check("the newest entry's title is in it", _all[0].title.encode() in r.data)
    check("it sits outside <main>",
          r.data.index(b"</main>") < r.data.index(b'whats-new-title'),
          "a dialog inside <main> is inerted along with it")
    check("it follows onto other admin pages",
          b'whats-new-title' in anon.get("/admin/resources").data)

    check("acknowledging it without CSRF is refused",
          anon.post("/admin/whats-new/seen", data={}).status_code == 400)
    r = anon.post("/admin/whats-new/seen",
                  data={"csrf_token": _token(anon.get("/admin/").data), "next": "/admin/resources"})
    check("acknowledging it redirects back", r.status_code == 302, f"-> {r.status_code}")
    check("it goes back where they were", "/admin/resources" in r.headers.get("Location", ""))
    check("it is gone afterwards", b'whats-new-title' not in anon.get("/admin/").data)
    check("and stays gone on a later page", b'whats-new-title' not in anon.get("/admin/topics").data)

    print("\n-- youtube sync (add new uploads)")
    # No network here on purpose: a smoke test that reaches YouTube fails when
    # YouTube is slow, not when the site is broken. The feed PARSING and the
    # "what is new" decision are what can regress, so those are tested against
    # a fixture; reachability is a deploy-time check, not a test.
    from services import youtube_discover as _yd
    from services import youtube_refresh as _yr

    _FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <yt:videoId>AAAAAAAAAAA</yt:videoId>
    <published>2026-09-16T21:00:16+00:00</published>
    <media:group>
      <media:title>A Brand New Episode</media:title>
      <media:description>Primary text: John 3:16

The hook paragraph before the first divider.
_____________________________________
Timestamps
0:00 - Opening
1:30 - The point</media:description>
      <media:thumbnail url="https://i.ytimg.com/vi/AAAAAAAAAAA/hqdefault.jpg"/>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>BBBBBBBBBBB</yt:videoId>
    <published>2026-09-13T13:00:05+00:00</published>
    <media:group>
      <media:title>A Brand New Short</media:title>
      <media:description>Short and sweet.</media:description>
    </media:group>
  </entry>
</feed>"""

    parsed = _yd.parse_feed(_FEED)
    check("the feed parses", len(parsed) == 2, f"got {len(parsed)}")
    check("it reads the title", parsed[0].title == "A Brand New Episode", parsed[0].title)
    check("it reads the full description", "John 3:16" in parsed[0].description)
    check("it reads the published date (naive UTC)",
          parsed[0].published is not None and parsed[0].published.tzinfo is None
          and parsed[0].published.year == 2026)
    check("malformed xml yields nothing rather than raising",
          _yd.parse_feed(b"<not xml") == [])

    with app.app_context():
        from models import Series, Teaching, Topic, db as _db

        shorts_series = Series.query.filter_by(kind="shorts").first()
        real = {t.youtube_id for t in Teaching.query.all()}

        # find_new(): already-present ids are skipped, and so is anything a
        # re-check has marked gone — that is the "local delete resurrected by
        # the next pull" trap the house rule warns about.
        an_existing = next(iter(real))
        feed_items = list(parsed) + [_yd.FeedItem(youtube_id=an_existing, title="Already here",
                                                  description="", published=None)]
        orig_uploads, orig_series, orig_missing = (
            _yd.channel_uploads, _yd.series_by_video, _yr.missing_video_ids)
        _yd.channel_uploads = lambda: list(feed_items)
        _yd.series_by_video = lambda: ({}, {"BBBBBBBBBBB"})
        _yr.missing_video_ids = lambda: {"AAAAAAAAAAA"}
        try:
            only_short = _yd.find_new()
            check("a video already on the site is not offered again",
                  all(i.youtube_id != an_existing for i in only_short))
            check("a video a re-check found GONE is never re-added",
                  all(i.youtube_id != "AAAAAAAAAAA" for i in only_short))
            check("what is left is the genuinely new one",
                  [i.youtube_id for i in only_short] == ["BBBBBBBBBBB"],
                  str([i.youtube_id for i in only_short]))
            check("the Shorts playlist decides the kind",
                  only_short and only_short[0].kind == "short")

            # add(): the row and everything derived from the description.
            _yr.missing_video_ids = lambda: set()
            _yd.series_by_video = lambda: ({"AAAAAAAAAAA": shorts_series} if False else {}, set())
            _yr.refresh_thumbnail = lambda vid, editor="": "skipped"
            _yr.thumb_url = lambda vid: None
            made = _yd.add(parsed[0], editor="smoke@example.com")
            _db.session.commit()
            check("the new episode is created", made.id is not None)
            check("its slug is readable", made.slug == "a-brand-new-episode", made.slug)
            check("it is a full episode, not a Short", made.kind == "teaching")
            check("the description was stored", "John 3:16" in (made.description or ""))
            from models import Chapter as _Ch, ScriptureRef as _Ref
            check("chapters came out of the description",
                  _Ch.query.filter_by(teaching_id=made.id).count() == 2,
                  str(_Ch.query.filter_by(teaching_id=made.id).count()))
            check("Scripture references came out of it too",
                  _Ref.query.filter_by(teaching_id=made.id).count() >= 1)
            check("running it again adds nothing",
                  all(i.youtube_id != "AAAAAAAAAAA" for i in _yd.find_new()))
        finally:
            _yd.channel_uploads, _yd.series_by_video = orig_uploads, orig_series
            _yr.missing_video_ids = orig_missing

    print("\n-- topics on the episode page")
    with app.app_context():
        from models import Teaching, Topic, db as _db
        a_short = Teaching.query.filter_by(kind="short").first()
        a_topic = Topic.query.first()
        short_id, topic_slug = a_short.id, a_topic.slug

    r = anon.get(f"/admin/teachings/{short_id}")
    check("the picker is on a Short's page too", b'name="topics"' in r.data,
          "the owner asked for episodes AND Shorts")
    check("it posts the topics action", b'value="topics"' in r.data)

    r = anon.post(f"/admin/teachings/{short_id}", data={
        "csrf_token": _token(anon.get(f"/admin/teachings/{short_id}").data),
        "action": "topics", "topics": topic_slug})
    check("saving topics redirects", r.status_code == 302, f"-> {r.status_code}")
    with app.app_context():
        from models import AdminEdit, Teaching, Topic, db as _db
        check("the Short is now on that topic",
              Topic.query.filter_by(slug=topic_slug).first() in
              _db.session.get(Teaching, short_id).topics)
        # It must also survive a re-seed, which wipes topics and replays
        # admin_edits — an assignment saved only on the row is lost there.
        row = AdminEdit.query.filter_by(entity_type="topic", entity_key=topic_slug).first()
        check("the assignment was recorded for the re-seed to replay",
              row is not None and "youtube_ids" in (row.payload or ""),
              "topic edit not recorded")

    check("admin pages renamed to Episodes & Shorts",
          b"Episodes &amp; Shorts" in anon.get("/admin/teachings").data)
    check("the Sync with YT button is on the page",
          b"Sync with YT" in anon.get("/admin/teachings").data)

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
