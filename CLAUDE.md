# The Wisdom Crucible — thewisdomcrucible site

Public ministry website for **The Wisdom Crucible with Jakob McClain**
(youtube.com/@TheWisdomCrucible). A structured teaching library — series,
Scripture, and topic navigation over episodes that unify video, podcast audio,
study notes, and manuscript in one page per teaching.

This project was built to the house conventions distilled from Data-Dungeon and
Talent Booker (the owner's reference projects). Rules below exist because they
shipped bugs elsewhere; keep them.

## Stack

- Flask 3.1.x + Flask-SQLAlchemy, server-rendered Jinja MPA. No SPA framework.
- `app.py` = `create_app()` factory + module-level `app` for `gunicorn app:app`.
  Blueprints from day one (`routes/public.py`, `routes/explore.py`) — Talent
  Booker's 1.1MB app.py is the anti-pattern this layout prevents.
- SQLite (WAL, per-connection PRAGMAs via connect event) on a `DATA_DIR`
  volume; `DATABASE_URL` env can point at Postgres. `postgres://` is normalised
  to `postgresql://`.
- Compiled Tailwind 3.4 (`npm run build:css`), never the Play CDN. Alpine.js
  vendored at `static/js/alpine.min.js`. No htmx, no bundler, no npm runtime deps.
- Exact-pinned `requirements.txt` including load-bearing transitives; dev-only
  tools live in `requirements-dev.txt` (yt-dlp, youtube-transcript-api).
- Deploy: Railway — `Procfile` + `railway.json` + `runtime.txt` +
  `.python-version`. `/healthz` is auth-free, DB-free, template-free.

## Content model (the load-bearing design decision)

**The teaching is the central entity.** Video, podcast audio, study notes,
manuscript, transcript, chapters, scripture refs, and topics all hang off one
`Teaching` row — one page per episode, never parallel per-medium libraries.

- `Series` mirrors the YouTube playlists exactly (the three teaching playlists
  + Shorts). `Teaching.kind` separates `teaching` from `short`; shorts are
  available but de-emphasised everywhere.
- `TranscriptSegment` keeps **timestamped** auto-caption segments per teaching,
  indexed by SQLite FTS5 (`services/search.py`). This is what makes the
  long-term goals — clip search, natural-language search, timestamp deep links —
  incremental features instead of rebuilds. Never flatten transcripts to a blob.
- `ScriptureRef` stores book/chapter/verse spans (parsed by `bible.py` from the
  descriptions' `Primary text:` / `Reference Text:` lines) + the verbatim
  display text. Explore-Scripture navigates Bible → testament → book → chapter.
- Content flows FROM YouTube: `scripts/sync_youtube.py` re-scrapes the channel
  and upserts (needs requirements-dev). The owner should never have to
  hand-maintain what his uploads already say. Manuscripts are the exception:
  drop markdown into `content/manuscripts/<slug>.md` and run
  `scripts/import_manuscripts.py`.
- Podcast buttons ALWAYS link Spotify (`PODCAST_URL`) — Spotify is the podcast
  source of truth, never the YouTube podcast playlists (owner's rule).
- Study notes self-host at `static/notes/<slug>.pdf`; `/notes/<slug>` is the
  stable URL (falls back to the description's external link) — safe to paste
  into YouTube descriptions.
- The auto transcript is INDEXED for search but never displayed — the
  Manuscript (Jakob's own script) is the readable form.
- Explore-Scripture lights a book only when it is the PRIMARY passage of a
  full teaching; passing citations don't count as "opened".

## UI rules (hard)

- **Dark only.** Canvas `#0a0f1a`. Every `<html>` tag (base.html AND any
  standalone page) carries `style="background:#0a0f1a;color-scheme:dark;"` —
  inline on the tag, because `<style>` in head is too late to stop the white
  flash between MPA navigations. The owner is photophobic; a white flash is a
  shipped bug.
- Palette: `surface` (blue-gray darks) + `flame` (blue accent) scales in
  `tailwind.config.js`. UI chrome stays blue/gray/white — red/gold/green may
  appear inside artwork only. Canonical border: `border-white/10`.
- No native `<select>` for pickers (Alpine dropdown + hidden input so plain GET
  submission still works). No `confirm()`/`alert()`. No emoji as icons — inline
  line SVGs via `templates/_icon_macros.html` (24×24, stroke=currentColor,
  stroke-width 1.6, round caps, aria-hidden).
- No autoplay ever; video embeds are click-to-load facades
  (youtube-nocookie.com iframe injected on click). Lightbox overlays for cards,
  inline player on the episode page.
- Motion is restrained: fade/rise entrances, subtle flame glow. Honor
  `prefers-reduced-motion`.
- Mobile first-class: base `grid-cols-1` on every responsive grid, 44px tap
  targets, inputs forced to 16px under 640px (iOS zoom), no horizontal
  scrollbars, no hover-only controls. Hamburger drawer mirrors the desktop nav —
  an entry added to one and not the other is a shipped bug.
- Section grammar: `.section` > `.section-title` (uppercase, letter-spaced,
  flame accent). Empty states are honest gray copy, never "Coming soon".
- Base `grid-cols-1` on every responsive grid. RECORDED EXCEPTION: the
  Explore-Scripture book grid uses base `grid-cols-2` — 66 one-line book names
  in a single column is worse mobile UX than two narrow columns; cells keep
  the 44px min tap height.
- Asset URLs carry `?v={{ ASSET_V }}` (md5 content hash computed at boot, NOT
  timestamps — timestamp versions bust every cache on every deploy).

## Engineering rules (hard)

- Store UTC; convert at display time via Jinja filters only.
- Typed form parsing (`request.form.get(x, type=int)`); public form fields are
  stripped + truncated to column bounds (SQLite ignores VARCHAR limits,
  Postgres raises).
- Contact form: honeypot field (silent success for bots), DB row saved BEFORE
  the email attempt, `send_email_safe` (daemon thread, app-context, logs both
  outcomes, never raises into the request), send guarded by
  `if app.config.get("MAIL_USERNAME")`, PRG redirect after.
- Every FK declares `ondelete` (+ matching ORM `cascade="all, delete-orphan"`
  for owned children); FK columns get `index=True`.
- New columns on existing tables go in `COLUMNS_TO_ADD` in
  `services/schema_migrations.py` — `db.create_all()` never adds columns.
  Prefer a new side table over a new column.
- CSRF on every POST (Flask-WTF, `WTF_CSRF_TIME_LIMIT=None`);
  `rel="noopener"` on every `target="_blank"`; alt on every img; real label on
  every input.
- Errors: 404/500 handlers return dependency-free inline HTML (a broken
  template must not break the error page); 500 rolls back the session first.
- `python smoke_test.py` gates every commit — it sets a throwaway SQLite
  `DATABASE_URL` **before importing app** (Config reads env at import time),
  seeds, and walks every route. `python scripts/check_url_for_endpoints.py`
  statically checks template `url_for()` targets (conditional branches hide
  BuildErrors from happy-path testing).
- Commits: `feat(area): change` style, < 70 chars, never amend, never
  `--no-verify`. Append user-visible changes to DEVLOG.md (newest first).

## Future features the structure already accommodates

- Timestamped clip search & NL search → extend `services/search.py` over the
  existing FTS tables.
- Publications/books tab → new side table + route; nav has room.
- Analytics → add a request-log side table or external analytics; nothing to restructure.
- Merch/donations → new blueprint; footer has room.
- Newsletter: deliberately NOT built (owner's explicit request — no recurring
  content obligations).

## Commands

```
npm run build:css                       # rebuild static/css/tailwind.css
python scripts/seed_db.py               # (re)build DB from data/seed/*.json
python scripts/sync_youtube.py          # re-scrape channel -> data/seed + upsert (dev deps)
python scripts/import_manuscripts.py    # content/manuscripts/*.md -> Teaching.manuscript
python smoke_test.py                    # full route walk, throwaway DB
python scripts/check_url_for_endpoints.py
python app.py                           # dev server (reloader OFF on purpose: SQLite locks)
```

Env vars (all optional locally): `SECRET_KEY`, `DATA_DIR`, `DATABASE_URL`,
`MAIL_SERVER/PORT/USE_TLS/USERNAME/PASSWORD/DEFAULT_SENDER`,
`CONTACT_RECIPIENT` (default thewisdomcrucible@gmail.com), `TWC_FORCE_HTTPS`.
