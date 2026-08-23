"""Owner-replaceable images (the non-episode artwork).

Episode thumbnails come from YouTube; these do not — the crucible mark, the
footer seal, Jakob's portrait, and the two artwork tiles on the home page.
Each is a SLOT with a committed default; uploading replaces it, removing puts
the original back.

Storage rule, same as study notes: uploads live on the DATA_DIR volume, never
under static/. Anything in static/ is baked into the container image and is
destroyed on the next deploy. The stored file is named after the slot, never
after the uploaded filename.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from models import SiteContent, db

# (slot, label, committed default under static/, help)
SLOTS = [
    ("logo", "Crucible logo", "img/logo-mark.png",
     "The mark in the header, on the home page, on About, and the browser-tab icon."),
    ("seal", "Footer seal", "img/twc-seal.png",
     "The gold seal at the foot of every page."),
    ("portrait", "Portrait of Jakob", "img/jakob.jpg",
     "Your photo on the About page."),
    ("yt_avatar", "YouTube avatar", "img/yt-avatar.jpg",
     "The round channel picture in “Find TWC elsewhere” on the home page."),
    ("podcast_art", "Podcast artwork", "img/podcast-art.jpg",
     "The podcast cover in “Find TWC elsewhere” on the home page."),
]
BY_SLOT = {slot: (label, default, help) for slot, label, default, help in SLOTS}

# Raster only. SVG is deliberately excluded: it can carry script, and these
# files are served from the site's own origin.
EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
}
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def images_dir() -> Path:
    from config import DATA_DIR

    path = Path(DATA_DIR) / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_key(slot: str) -> str:
    return f"image.{slot}"


def stored(slot: str) -> tuple[str, str] | None:
    """(extension, version) for an uploaded image, or None for the default."""
    row = SiteContent.query.filter_by(key=_record_key(slot)).first()
    if row is None or not (row.value or "").strip():
        return None
    ext, _, version = row.value.partition("|")
    if ext not in EXTENSIONS or not (images_dir() / f"{slot}{ext}").is_file():
        return None
    return ext, (version or "1")


def path_for(slot: str) -> Path | None:
    found = stored(slot)
    return images_dir() / f"{slot}{found[0]}" if found else None


def mimetype_for(ext: str) -> str:
    return _MIME.get(ext, "application/octet-stream")


def is_custom(slot: str) -> bool:
    return stored(slot) is not None


def save(slot: str, storage, editor: str = "") -> str | None:
    """Store an uploaded image for a slot. Returns an error, or None."""
    if slot not in BY_SLOT:
        return "That image doesn't exist."
    ext = Path(storage.filename or "").suffix.lower()
    if ext not in EXTENSIONS:
        return "Please choose a PNG, JPG, or WEBP image."

    data = storage.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        return "That image is larger than 8MB."
    if not data:
        return "That file was empty."
    if not any(data.startswith(sig) for sig in _MAGIC.get(ext, ())):
        return f"That doesn't look like a real {ext} image."

    directory = images_dir()
    # One file per slot: clear the other extensions so a PNG replacing a JPG
    # can't leave both behind.
    for other in EXTENSIONS:
        stale = directory / f"{slot}{other}"
        if stale.is_file():
            stale.unlink()
    (directory / f"{slot}{ext}").write_bytes(data)

    row = SiteContent.query.filter_by(key=_record_key(slot)).first()
    if row is None:
        row = SiteContent(key=_record_key(slot))
        db.session.add(row)
    # The version stamp is the cache buster — the URL must change or browsers
    # keep serving the old picture.
    row.value = f"{ext}|{int(time.time())}"
    row.updated_by = (editor or "")[:320]
    db.session.commit()
    logging.info("admin: replaced image %r (%d bytes)", slot, len(data))
    return None


def restore(slot: str) -> bool:
    """Drop the upload and go back to the image the site shipped with."""
    removed = False
    directory = images_dir()
    for ext in EXTENSIONS:
        path = directory / f"{slot}{ext}"
        if path.is_file():
            path.unlink()
            removed = True
    SiteContent.query.filter_by(key=_record_key(slot)).delete()
    db.session.commit()
    return removed
