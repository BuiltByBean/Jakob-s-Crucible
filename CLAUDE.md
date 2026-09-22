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
- Auto-deploy is driven by a DEPLOYMENT TRIGGER, which is a SEPARATE thing
  from the service's source repo. The service can show the correct repo and
  still never build: from 2026-08-26 to 2026-09-09 this project had a source
  but ZERO triggers, so a push produced no webhook, no build, and no error —
  just silence, while `railway status` happily showed the right repo. Triggers
  are not in `railway status`; query backboard for
  `project { deploymentTriggers { edges { node { repository branch } } } }`.
  `checkSuites` stays FALSE here: there is no CI in this repo, so waiting on a
  check suite would wait for something that never reports.

## Content model (the load-bearing design decision)

**The teaching is the central entity.** Video, podcast audio, study notes,
manuscript, transcript, chapters, scripture refs, and topics all hang off one
`Teaching` row — one page per episode, never parallel per-medium libraries.

- `Series` mirrors the YouTube playlists exactly (the four teaching playlists
  + Shorts — count lives in `SERIES_ORDER` in scripts/seed_db.py, which is the
  single place to touch when Jakob reorganises playlists). `Teaching.kind`
  separates `teaching` from `short`; shorts are available but de-emphasised
  everywhere. Shorts never show hashtags, and get related teachings only from
  links Jakob writes into the Short's own description. Shorts DO carry topics
  as of 2026-09-22 — the owner reversed his own earlier "shorts never take
  topics" rule and asked for the picker on Shorts by name. The exclusion had
  to come out of THREE places (the topic screen's query, its save, and the
  admin_edits replay); leaving it in the replay would have silently undone
  every tagged Short on the next re-seed.
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
- The episode page's right-hand rail is RELATED EPISODES (owner's request,
  2026-09-22): every other full episode sharing at least one topic, newest
  first, self excluded and de-duplicated (an episode sharing two topics must
  not appear twice — the query is a join, so it needs `.distinct()`). It is
  hidden entirely when the episode has no topics rather than rendering an
  empty box; the owner tags episodes from the Topics picker on each episode's
  admin page. A SHORT keeps the old chronological "In this series" rail — the
  standing rule that a Short's related content is only ever what Jakob links
  by hand still holds, so nothing there is generated from shared topics.
- Explore-Scripture lights a book only when it is the PRIMARY passage of a
  full teaching; passing citations don't count as "opened".
- The Statement of Faith stays ONE markdown field; `services/statement.py`
  derives the page's structure from two conventions in it at render time —
  `### Heading` is an article, `### A. Heading` nests inside the article above
  it, and a `**Scripture References:**` line becomes the tappable chips. Never
  turn it into a second table the owner has to maintain. Refs are parsed
  SERVER-side (CCC does it in the browser) so the chips are in the first paint.
  The semicolons in a `**Scripture References:**` line are the DELIMITER, not
  decoration: they are what splits one reference from the next, and they are
  deliberately NOT rendered between the resulting chips. Never render a mark
  the owner has to keep typing — he will delete it from the admin box, and the
  chips silently become prose (he tried exactly that).
  `static/js/scriptures.js` is the ESV bundle AND the single source of truth for
  which passages are clickable — `bundled_keys()` reads that same file, so a
  reference with no text renders as plain text instead of a dead button. It is
  held under Crossway's 500-verse ceiling on purpose (91 passages, 441 verses;
  1 Corinthians 12–14 is left out) — check the count before adding to it.

## Admin area (/admin)

The owner maintains the site here; a code change should never be needed for
wording, links, resources, topics, manuscripts, or notes.

- Hand-rolled session auth in `services/auth.py` (no Flask-Login). ONE
  `before_request` on the blueprint guards every route — a new admin route is
  protected by default. Nothing mutates on GET (base.html prefetches internal
  links, which would fire GET mutations on hover).
- `session_epoch` on the user row is copied into the session and compared per
  request: changing a password signs out every other device. Absolute (12h)
  and idle (2h) expiry are enforced in the same guard.
- Login throttling is DB-backed (`login_attempts`), keyed on BOTH IP and
  email — gunicorn runs multiple workers, so an in-process dict would throttle
  only the worker that served the request. IP = RIGHTMOST X-Forwarded-For.
- **Admin tables must never declare an FK to teachings/series/topics.** The
  re-sync wipes those tables and SQLite enforces CASCADE, which would empty
  the admin data. Reference content by `youtube_id`/slug instead.
- **Editable content lives in the DB, never in repo files.** The container
  filesystem is rebuilt from git on deploy; only DATA_DIR persists. The
  statement of faith moved from `content/statement_of_faith.md` into
  `site_content` (seeded once at boot). Uploaded notes go to DATA_DIR/notes
  keyed by youtube_id, never under `static/`.
- Every admin edit to content the re-sync rebuilds is ALSO recorded in
  `admin_edits` (JSON payload keyed by slug/youtube_id) and replayed by
  `services/admin_edits.apply_all()` at the end of `seed_db.py`. That is what
  makes a YouTube sync non-destructive — smoke-tested end to end.
- Page copy is a registry (`services/site_content.py`): every key declares the
  shipped default, so the site renders identically until edited and clearing a
  field restores the original wording. Templates call `content('key')`.
- The episode DESCRIPTION is hand-editable and nothing overwrites it. Neither
  "Sync with YT" (which only ever creates rows) nor "Re-check" touches it —
  the description branch was deliberately cut out of `refresh_teaching` on
  2026-09-22, API-key path and all, because a re-check that silently replaced
  the owner's edit is the one thing a re-check must never do. Saving it re-runs
  `apply_description`, so summary, chapters and Scripture refs all re-derive
  through the same parser an import uses. It is recorded in `admin_edits` and
  replayed UNCONDITIONALLY (unlike the manuscript, which only restores when
  empty): a re-seed always rewrites the description from the seed, so
  "restore only if blank" would never fire and his edit would vanish.
- `teachings.closing_note` overrides the closing section parsed out of the
  description; empty means "use the description's". It is a column (in
  COLUMNS_TO_ADD) replayed through admin_edits like the manuscript.
- The three "New here? Start with these" cards can be chosen by hand on the
  Featured Episode screen. They live in `home.start_picks` as comma-separated
  youtube_ids — deliberately NOT a content-registry entry, because ids are not
  wording and it must not appear as a text box on a page-wording screen. Empty
  means the automatic trio (channel intro, Statement of Faith, featured). The
  three blurbs are per SLOT now, not per meaning, though the keys kept their
  old names so existing edits survived.
- Admin-supplied URLs are scheme-checked on write AND re-checked at render
  (`|safe_url`) — an admin field reaching an `href` is the real stored-XSS
  surface. Admin prose renders through `manuscript_html`/`rich_text`, which
  escape before applying markup.
- Non-episode artwork (mark, seal, portrait, the two home tiles) is
  replaceable from /admin/images: slots in `services/site_images.py`, files on
  DATA_DIR keyed by slot, served by `/media/<slot>` with a version stamp, and
  templates call `site_image('slot')`. Raster only — SVG is excluded because
  it can carry script and is served from our own origin.
- `services/youtube_discover.py` is how a NEW upload reaches the site without
  a developer: the channel's Atom feed
  (`feeds/videos.xml?channel_id=…`) answers from the production container and
  carries FULL descriptions — verified byte-for-byte against what the yt-dlp
  scrape had stored. That is the one YouTube surface measured to work from the
  datacenter IP besides oEmbed and i.ytimg.com. Two limits are structural: the
  feed holds the 15 most recent uploads (a catch-up tool, never a backfill —
  `scripts/sync_youtube.py` still owns history), and it carries no duration, so
  Short-vs-episode is decided by membership of the owner's own Shorts playlist
  feed, which is fetched anyway to place a new episode in its series. Upsert is
  on `youtube_id`, and a video a re-check has marked GONE is never re-added —
  otherwise the sync resurrects exactly what the re-check just buried.
- `services/youtube_refresh.py` is the runtime subset of the channel sync:
  re-check one episode or the whole library from /admin. **What YouTube
  answers from the production container was MEASURED, not assumed** — oEmbed
  (title + "does this still exist") and i.ytimg.com thumbnails work; the watch
  page bot-walls and InnerTube returns LOGIN_REQUIRED. Both scrapes succeed
  from a home connection, so never "verify" this locally. Descriptions
  therefore refresh only when the optional `YOUTUBE_API_KEY` is set. Refreshed
  thumbnails go on DATA_DIR (`/media/thumb/<youtube_id>?v=<content hash>`) and
  `_build_teaching` prefers them, so a re-seed keeps them. A thumbnail is only
  "updated" when its bytes differ from what the site is ALREADY showing
  (volume copy, else the committed static file) — otherwise the first re-check
  reports every video as changed.
- The library sweep runs on a daemon thread and writes progress to
  `site_content` under `youtube.sweep`, so any gunicorn worker can answer the
  poll; 30-odd videos outrun the 60s request timeout.
- Re-pointing an episode at a re-uploaded video (`repoint()`) must carry the
  admin_edits records, uploaded notes and topic assignments across — they all
  key on `youtube_id`, which is precisely what a re-upload changes.
- Contact messages can be hard-deleted. Destructive admin buttons confirm
  themselves in place (`x-data="{ sure: false }"`), never with `confirm()`.
- The what's-new dialog reads DEVLOG.md (`services/release_notes.py`), which
  ships inside the image (`COPY . .`), so a deploy carries its own release
  notes and there is no second changelog to keep. Entries are ordered by
  POSITION in the file, never by date — several entries already share one.
  DEVLOG is hard-wrapped at ~78 columns and `manuscript_html` turns a single
  newline into `<br>`, so the body is reflowed before rendering or every
  sentence breaks at whatever column the file wrapped. The per-maintainer
  marker is a side table (`admin_release_seen`) keyed by email, so create_all
  makes it on deploy with no migration entry; someone with no marker sees only
  the NEWEST entry, never the whole archive.
- Accounts are created by `scripts/seed_admin.py` (never resets an existing
  password) and recovered with `scripts/reset_admin_password.py`. No
  email-based reset: mail is a silent no-op unless MAIL_USERNAME is set.

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
  stroke-width 1.6, round caps, aria-hidden). Collapsible sections use an
  Alpine `x-show`/`x-collapse` toggle, never `<details>`.
- **No inline `<script>` in a template** — the CSP allows `'unsafe-eval'` (for
  Alpine's `x-*` expressions) but NOT `'unsafe-inline'`, so an inline block is
  silently blocked. Alpine components live in `static/js/admin.js` (loaded
  deferred BEFORE alpine.min.js) and take their server data from `data-*`
  attributes — never from `| tojson` inside a double-quoted attribute.
- A dialog goes in `{% block overlays %}`, NOT in the content block: the
  overlay scripts inert `header`/`main`/`footer` while one is open, so a dialog
  rendered inside `<main>` inerts itself. A Tailwind class survives the build
  only if it appears in a TEMPLATE — a class used solely from a JS file is
  purged, so style JS-built markup by element under a parent class (see
  `.scripture-passage p`). `[hidden]` is forced `display:none !important` in
  the base layer: any display utility on an element otherwise beats the
  browser's own `[hidden]` rule, and `el.hidden = true` silently does nothing.
- Quoted passages in manuscripts and descriptions are delimited: `>` opens and
  `<` CLOSES. Blocks split on blank lines, so a bare per-line `>` broke a long
  quote into one blockquote per paragraph and lost line breaks inside it. A `>`
  with NO closing `<` still renders the old way, which is what kept the 200-odd
  quotes already written in the manuscripts rendering unchanged — do not
  "tidy" that fallback away. `manuscript_html` now renders episode descriptions
  too (summary, context, closing), so both boxes obey one set of rules;
  checked first that no existing description had a line that the block grammar
  would reinterpret as a heading or list.
- The home page's "Recent episodes" always shows SIX full episodes and never a
  Short. It reads seven, because the lead card may be one of them — filtering
  in the template silently showed five whenever the newest episode was also
  the featured one, which is most of the time.
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
- **Form actions sit on the RIGHT** (owner's rule: most people are
  right-handed): `flex items-center justify-end gap-3`, secondary action to
  the LEFT of the primary. Applies to the public contact form and every admin
  form.
- Long PICK-lists inside a form scroll inside themselves (`.scroll-list`,
  which sets `overscroll-behavior: contain` so hitting the end doesn't start
  scrolling the page). Browse lists keep normal page scrolling — never nest a
  page's main content in an inner scroller.
- The atmosphere layers (sparks canvas, grain, glow) render site-wide from
  base.html and are each switchable from /admin (`appearance.*` toggles).
  Sparks stay off under `prefers-reduced-motion` regardless of the switch.
- Base `grid-cols-1` on every responsive grid. RECORDED EXCEPTION: the
  Explore-Scripture book grid uses base `grid-cols-2` — 66 one-line book names
  in a single column is worse mobile UX than two narrow columns; cells keep
  the 44px min tap height.
- The birthday greeting is DATE-GATED, not a switch someone has to remember to
  turn off: `birthday.*` in the content registry holds the MM-DD, the wording
  and the fireworks toggle, and `services/birthday.py` returns None on all 364
  other days — so base.html emits no markup and no script at all. The date is
  read on the ministry's own clock (America/Chicago, which is why `tzdata` is
  pinned), because comparing UTC dates lights the greeting at 7pm the evening
  before and drops it at 7pm on the day itself.
- A canvas sized from `clientWidth` must be re-measured with a ResizeObserver,
  NOT just `window.resize`: the element's box changes with no window resize at
  all (a container reflow, a late web font, a scrollbar, a phone's URL bar),
  and the first reading can land mid-layout. Measured once and left alone, a
  32px first reading aimed every firework into the top-left corner.
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
python scripts/seed_admin.py            # create the admin accounts (idempotent)
python scripts/reset_admin_password.py <email> <pw>   # forgotten-password recovery
python scripts/seed_db.py               # (re)build DB from data/seed/*.json
python scripts/sync_youtube.py          # re-scrape channel -> data/seed + upsert (dev deps)
                                        # run from a HOME connection: YouTube
                                        # bot-walls datacenter IPs
python scripts/import_manuscripts.py    # content/manuscripts/*.md -> Teaching.manuscript
python smoke_test.py                    # full route walk, throwaway DB
python scripts/check_url_for_endpoints.py
python app.py                           # dev server (reloader OFF on purpose: SQLite locks)
```

Env vars (all optional locally): `SECRET_KEY`, `DATA_DIR`, `DATABASE_URL`,
`MAIL_SERVER/PORT/USE_TLS/USERNAME/PASSWORD/DEFAULT_SENDER`,
`CONTACT_RECIPIENT` (default thewisdomcrucible@gmail.com), `TWC_FORCE_HTTPS`,
`YOUTUBE_API_KEY` (optional; only unlocks description refresh in /admin).
