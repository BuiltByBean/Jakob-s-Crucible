"""Re-scrape the YouTube channel into data/seed/*.json, then re-seed the DB.

The site's content flows FROM the channel — new uploads, edited descriptions,
and new playlists land here without hand-maintenance. Requires the dev deps
(pip install -r requirements-dev.txt). Run: python scripts/sync_youtube.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

CHANNEL = "https://www.youtube.com/@TheWisdomCrucible"
SEED_DIR = REPO / "data" / "seed"


def scrape() -> None:
    import yt_dlp
    from youtube_transcript_api import YouTubeTranscriptApi

    flat_opts = {"quiet": True, "extract_flat": True, "skip_download": True}
    full_opts = {"quiet": True, "skip_download": True}

    def flat(url):
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            return ydl.extract_info(url, download=False)

    def full(url):
        with yt_dlp.YoutubeDL(full_opts) as ydl:
            return ydl.extract_info(url, download=False)

    print("Playlists...", flush=True)
    playlists = []
    for entry in flat(f"{CHANNEL}/playlists").get("entries", []):
        detail = flat(entry["url"])
        playlists.append({
            "id": detail.get("id"),
            "title": detail.get("title"),
            "description": detail.get("description"),
            "url": entry["url"],
            "video_ids": [e["id"] for e in detail.get("entries", [])],
            "video_titles": [e.get("title") for e in detail.get("entries", [])],
        })
    (SEED_DIR / "playlists.json").write_text(
        json.dumps(playlists, indent=1, ensure_ascii=False), encoding="utf-8")

    print("Videos...", flush=True)
    all_ids: dict[str, str] = {}
    for tab in ("videos", "shorts", "podcasts"):
        try:
            for e in flat(f"{CHANNEL}/{tab}").get("entries", []):
                all_ids.setdefault(e["id"], tab)
        except Exception as ex:  # noqa: BLE001
            print(f"  {tab} tab failed: {ex}", flush=True)
    for p in playlists:
        for vid in p["video_ids"]:
            all_ids.setdefault(vid, "playlist-only")

    videos = {}
    for i, (vid, src) in enumerate(all_ids.items(), 1):
        print(f"  [{i}/{len(all_ids)}] {vid}", flush=True)
        try:
            info = full(f"https://www.youtube.com/watch?v={vid}")
            videos[vid] = {k: info.get(k) for k in (
                "id", "title", "description", "duration", "upload_date", "timestamp",
                "chapters", "thumbnail", "view_count", "tags")}
            videos[vid]["source_tab"] = src
        except Exception as ex:  # noqa: BLE001
            videos[vid] = {"id": vid, "source_tab": src, "error": str(ex)}
        time.sleep(0.5)
    (SEED_DIR / "videos.json").write_text(
        json.dumps(videos, indent=1, ensure_ascii=False), encoding="utf-8")

    # Transcripts: keep existing ones, fetch only what's missing (YouTube
    # rate-limits transcript fetches aggressively — never re-pull the world).
    transcripts_path = SEED_DIR / "transcripts.json"
    transcripts = json.loads(transcripts_path.read_text(encoding="utf-8")) if transcripts_path.exists() else {}
    api = YouTubeTranscriptApi()
    for vid, v in videos.items():
        if "error" in v or v.get("source_tab") == "playlist-only":
            continue  # podcast duplicates share the video's audio
        if "segments" in transcripts.get(vid, {}):
            continue
        try:
            fetched = api.fetch(vid)
            transcripts[vid] = {
                "language": fetched.language_code,
                "is_generated": fetched.is_generated,
                "segments": [{"start": round(s.start, 2), "duration": round(s.duration, 2), "text": s.text}
                             for s in fetched],
            }
            print(f"  transcript {vid}: {len(transcripts[vid]['segments'])} segments", flush=True)
        except Exception as ex:  # noqa: BLE001
            print(f"  transcript {vid}: {type(ex).__name__}", flush=True)
        time.sleep(5)
    transcripts_path.write_text(json.dumps(transcripts, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    scrape()
    from scripts.fetch_thumbnails import run as fetch_thumbs
    from scripts.seed_db import run

    fetch_thumbs()
    run()
