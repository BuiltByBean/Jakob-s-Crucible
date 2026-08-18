# DEVLOG

User-facing change history, newest first.

## 2026-08-18 — Review pass (5 specialist reviewers, findings verified)

Fixed before first commit: both Psalm 110 episodes were missing their podcast
buttons (podcast pairing was nondeterministic when a podcast playlist also
contains the main video — now deterministic and always prefers the dedicated
podcast upload); search-term highlighting could corrupt its own markup for
queries containing "mark"; request bodies are now capped at 1MB; asset cache
busting now hashes file contents (mtime hashes would bust every visitor's
cache on every deploy); CSRF/400/405/413 errors now render the dark branded
page instead of a white default; contact form gained a per-IP rate limit and
newline flattening (a crafted subject could silently kill the notification
email); SQLite now enforces foreign keys; anchors no longer hide under the
sticky header; the video lightbox and mobile drawer keep keyboard focus
inside; home gained the "New here? Start with these" starting points.

## 2026-08-18 — Initial build

The whole site, one shot: dark scholarly design in the blue-white
flame/crucible branding; home with hero, latest/featured teaching, series
cards, and a Statement of Faith entry point; Teaching Library mirroring the
YouTube playlists (shorts available but de-emphasised); one page per teaching
with click-to-play video, podcast link, downloadable study notes, description,
clickable chapters, a seekable auto-generated transcript, scripture and topic
links, series rail, and related teachings; Explore Scripture
(testament → book → chapter with honest coverage counts); Explore Topics (six
curated themes); search across titles, series, passages, topics, and the
timestamped words spoken inside every teaching; Study Resources; contact form
that emails thewisdomcrucible@gmail.com with the message stored first.

Content is scraped from the channel (13 teachings, 19 shorts, chapters,
descriptions, notes links, 22 transcripts) and re-syncs with one script.
