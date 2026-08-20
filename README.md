# The Wisdom Crucible — ministry website

Public site for **The Wisdom Crucible with Jakob McClain**
([youtube.com/@TheWisdomCrucible](https://www.youtube.com/@TheWisdomCrucible)).

A structured teaching repository, not a channel mirror: one page per teaching
unifies the video, podcast audio, study notes, manuscript, chapters, and a
clickable timestamped transcript — navigable by **series**, **Scripture**
(testament → book → chapter), and a restrained set of **topics**, with a search
that reaches the words spoken inside each teaching.

## Quick start

```
pip install -r requirements.txt
npm install && npm run build:css      # compiled Tailwind (output is committed, so optional)
python scripts/seed_db.py             # build the DB from data/seed/*.json
python app.py                         # http://127.0.0.1:5000
```

## Content workflow (near-zero maintenance by design)

| What | How |
|---|---|
| New video/short/podcast uploaded | `pip install -r requirements-dev.txt`, then `python scripts/sync_youtube.py` — re-scrapes the channel and re-seeds. Everything (title, description, chapters, scripture refs, notes link, transcript) flows from the upload. |
| Publish a manuscript | Drop `content/manuscripts/<teaching-slug>.md`, run `python scripts/import_manuscripts.py`. Survives re-syncs. |
| Self-host study notes | Drop `static/notes/<teaching-slug>.pdf`. The stable URL `/notes/<teaching-slug>` serves it (and is safe to paste into YouTube descriptions — it falls back to the description's external link until the PDF exists). |
| Add a study resource | Add a row to `RESOURCES` in `scripts/seed_db.py` (or insert a `Resource` row) |
| Curate topics | Edit `TOPICS` / `TOPIC_MAP` in `scripts/seed_db.py` |

Content the site owns (manuscripts, manually added scripture refs) survives
re-seeding; everything else is rebuilt from the channel.

## Before commit

```
python smoke_test.py                     # seeds a throwaway DB, walks every page
python scripts/check_url_for_endpoints.py
```

## Deploy (Railway)

`Procfile` + `railway.json` are set. Mount a volume at `/data`, set env:
`SECRET_KEY`, `DATA_DIR=/data`, `TWC_FORCE_HTTPS=1`, and for the contact form
`MAIL_USERNAME` / `MAIL_PASSWORD` (Gmail app password) / `MAIL_DEFAULT_SENDER`.
Contact messages are stored in the DB even when mail is unconfigured/failing.

See [CLAUDE.md](CLAUDE.md) for the engineering conventions this project follows
and the future features (clip search, publications, analytics, store, giving)
its structure already leaves room for.
