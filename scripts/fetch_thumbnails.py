"""Download every video's thumbnail into static/img/thumbs/<youtube_id>.jpg.

Self-hosting thumbnails makes the site immune to i.ytimg.com throttling
(hotlinked thumbnails visibly 429'd under load on 2026-08-18), dead maxres
variants, and CDN URL churn. Tries maxresdefault first, falls back to
hqdefault (which exists for every video). Skips files already downloaded
unless --force. Run standalone or via scripts/sync_youtube.py.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
THUMB_DIR = REPO / "static" / "img" / "thumbs"


def fetch(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read() if r.status == 200 else None
    except Exception:  # noqa: BLE001 — 404 on maxres is the expected miss
        return None


def run(force: bool = False) -> None:
    videos = json.loads((REPO / "data" / "seed" / "videos.json").read_text(encoding="utf-8"))
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    ok = skipped = failed = 0
    for vid, v in videos.items():
        if "error" in v or v.get("source_tab") == "playlist-only":
            continue  # podcast duplicates never render a thumbnail
        dest = THUMB_DIR / f"{vid}.jpg"
        if dest.exists() and not force:
            skipped += 1
            continue
        data = fetch(f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg") or fetch(
            f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        )
        if data:
            dest.write_bytes(data)
            ok += 1
            print(f"  {vid}.jpg ({len(data) // 1024}KB)", flush=True)
        else:
            failed += 1
            print(f"  {vid}: FAILED both variants", flush=True)
    print(f"thumbnails: {ok} downloaded, {skipped} already present, {failed} failed")


if __name__ == "__main__":
    run(force="--force" in sys.argv)
