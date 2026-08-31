"""The birthday greeting — a dated, admin-controlled celebration overlay.

One day a year the site opens with fireworks and a greeting for Jakob. It is
deliberately DATE-GATED rather than a switch someone has to remember to turn
off: a greeting that outstays its day is worse than no greeting at all, so
the overlay disappears by itself when the date rolls past.

Everything here reads through the site_content registry (`birthday.*`), so
the date, the wording and the switch are all editable from /admin without a
code change — the house rule for anything the owner might want to reword.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from services import site_content as sc

# A birthday is a LOCAL-calendar event, not a UTC one. Railway runs the
# container in UTC, so comparing UTC dates would light the greeting at 7pm
# the evening before and drop it at 7pm on the day itself — visibly wrong on
# the one day it has to be right. The ministry is Central (the public number
# is a 254 area code), so that is the clock the date is read against.
MINISTRY_TZ = "America/Chicago"


def _today():
    """Today's date on the ministry's own clock, falling back to UTC.

    zoneinfo needs the system tz database, which slim container images do not
    always carry (`tzdata` is pinned in requirements.txt for exactly this).
    A missing database must not 500 the whole site over a greeting."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(MINISTRY_TZ)).date()
    except Exception as exc:  # noqa: BLE001 — never let a tz lookup break a page
        logging.warning("birthday: no tz database for %s (%s) — using UTC", MINISTRY_TZ, exc)
        return datetime.now(timezone.utc).date()


def parse_month_day(raw: str) -> tuple[int, int] | None:
    """'MM-DD' -> (month, day), or None if it isn't a real calendar date.

    02-29 is accepted: it is a real birthday. Falling back to the 28th in
    common years is handled by the caller, not by rejecting the value."""
    parts = (raw or "").strip().replace("/", "-").split("-")
    if len(parts) != 2:
        return None
    try:
        month, day = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not 1 <= month <= 12:
        return None
    days_in = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]
    if not 1 <= day <= days_in:
        return None
    return month, day


def is_today() -> bool:
    """True when the greeting should be showing right now."""
    if not sc.enabled("birthday.enabled"):
        return False
    parsed = parse_month_day(sc.content("birthday.date"))
    if parsed is None:
        return False
    month, day = parsed
    today = _today()
    # A 29 February birthday still gets its day in a common year: the 28th
    # stands in, which is the convention most calendars use.
    if month == 2 and day == 29 and today.month == 2 and today.day == 28:
        try:
            datetime(today.year, 2, 29)
        except ValueError:
            return True
    return today.month == month and today.day == day


def greeting() -> dict | None:
    """The greeting to render, or None on any other day.

    The `stamp` is what the browser remembers once the visitor has dismissed
    it, so the overlay opens once a year rather than on every navigation."""
    if not is_today():
        return None
    return {
        "headline": sc.content("birthday.headline"),
        "stamp": _today().isoformat(),
    }
