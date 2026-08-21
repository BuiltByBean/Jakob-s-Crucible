# DEVLOG

User-facing change history, newest first.

## 2026-08-21 — Home atmosphere: rising blue-white sparks

The home page (only) gains the Vault-of-Ash atmosphere recolored to the
crucible's blue-white flame: faint sparks rise and wobble across the whole
viewport, a film-grain texture settles over the canvas, and a soft blue
glow breathes up from beneath the fold. Sparks pause when the tab is
hidden and switch off entirely for reduced-motion visitors (the static
texture stays). Every other page keeps its plain dark canvas.

## 2026-08-21 — Gradient nav underline

The active nav item now wears a thin blue-to-white flame gradient underline
hugging its label (style borrowed from BuiltByBean), with full-white text —
replacing the old pill highlight. Applied to the desktop nav, the search
icon, and the mobile drawer alike.

## 2026-08-21 — Series split, shorts cleanup, manuscript search, X card

The Teaching Series now mirror the reorganized channel: Detailed &
Verse-by-Verse is split into "Verse-by-Verse Bible Studies" (Haggai, Psalm
110) and "Topical Bible Studies" (What Is Faith?), with Jakob's new
descriptions here and on Reflection & Application. Search now reports
phrase hits from inside the manuscripts ("Written in the manuscripts", each
hit opening that manuscript directly) alongside the spoken-word hits, so a
phrase is found whether it lives in the captions or Jakob's script. Shorts
are tidied per Jakob's rules: no topic labels, no YouTube hashtags anywhere
on the site, related teachings only where a Short's own description links a
full teaching, and the video overlay under a Short now says "View full
Short page". Home's Find-TWC-elsewhere X entry is a proper compact card
with the official X logo; the Navigate the Crucible descriptions are
Jakob's new wording; the crucible logo carries a slightly stronger
blue-white edge glow everywhere; the Scripture legend sits on one line on
desktop. All 19 shorts now have transcripts indexed (YouTube finally
permitted the last 10).

## 2026-08-21 — Clickable cards, topic reorganization, fuller Psalm 110 scripts

Episode cards are now fully clickable: anywhere on the text half opens the
episode page (title and scripture chips keep their own targets), while the
thumbnail half still pops the video overlay. Topics reorganized per Jakob's
new philosophy — Apologetics, Christian Living & Discipleship (merged),
Christology, Doctrine & Theology, and the new Special Episodes; Faith &
Trust retired; listings alphabetical; new descriptions throughout; Haggai
part 2 deliberately carries no topic. The three Psalm 110 manuscripts were
replaced with Jakob's updated scripts that now include the quoted passages
in full (word-preservation verified again), and their downloadable notes
were refreshed to match. Study Resources reworded and alphabetized; every
Statement of Faith card/button now routes to the Statement of Faith page.

## 2026-08-20 — Statement of Faith page, manuscripts everywhere, gold books

A new Statement of Faith section joins the navigation: the easy-to-read
confession with the Scriptures behind each conviction, and a pointer to the
full episode for the long form. All 13 teachings now carry Jakob's own
manuscript in blog form (converted from his NOTES documents with his spoken
line-cadence preserved), and 12 of them offer the notes as a self-hosted
download — the INTRODUCING episode deliberately reads-only. On the Scripture
shelves, a book whose every chapter has been expounded verse-by-verse now
wears its title in gold (currently Haggai) — computed from coverage, so
finishing a book golds it automatically.

## 2026-08-18 — Jakob's launch-review revisions

Official brand assets land: the real crucible mark in the header and hero,
the gold ministry seal closing the footer like an impressed wax seal, and
Jakob's genuine portrait on About with his own new bio and the revised
ministry description. Every podcast button now points at Spotify (the
podcast's home), and Home gains a "Find TWC elsewhere" section — YouTube
card with the channel avatar and spelled-out address, podcast card with
artwork and all four platforms, and the X handle. Explore Scripture now
lights a book only when it's the primary passage of a full teaching (with
counts to match), book pages say "expounding" / "referencing" with the
reference list collapsible, and the series rail on episode pages numbers
chronologically. Episode buttons read Watch on YouTube / Listen on Spotify /
Download Notes, with notes now hostable on the site at stable /notes/<slug>
URLs. The auto transcript is hidden (still searchable) in favor of
manuscripts. Resources drop TGC, revise Logos/STEP, and add Blue Letter
Bible. Assorted copy revisions across Library, Resources, and Contact.

## 2026-08-18 — Scripture library redesign + self-hosted thumbnails

Explore Scripture is now a library: canon divisions (The Law, History, Poetry
& Wisdom, the Prophets, Gospels & Acts, Letters…) render as engraved shelf
plaques with every book standing as a leather spine on a wooden board, sized
by its chapter count. Books the crucible has opened glow with the flame and
carry their teaching count; hovering pulls them off the shelf; the whole
case rises into place on load (and sits still for reduced-motion visitors).

Thumbnails are now self-hosted (scripts/fetch_thumbnails.py, wired into
sync) after YouTube's image CDN visibly throttled hotlinked thumbnails —
cards can no longer show empty tiles because of i.ytimg.com rate limits.

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
