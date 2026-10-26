"""Schedule math and phrase parsing for routines. Pure functions; local time."""

import re
from datetime import datetime, timedelta

from contracts.automation import Schedule

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_ALIASES = {d[:3]: i for i, d in enumerate(DAYS)} | {d: i for i, d in enumerate(DAYS)}


def local_now() -> datetime:
    return datetime.now().astimezone()


def _at(day: datetime, hhmm: str) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def next_run(schedule: Schedule, after: datetime) -> datetime | None:
    """The first run strictly after `after` (an aware local datetime), or None if finished."""
    if schedule.kind == "once":
        if schedule.at is None:
            return None
        when = datetime.fromtimestamp(schedule.at, tz=after.tzinfo)
        return when if when > after else None
    if schedule.kind == "interval":
        assert schedule.every_minutes
        return after + timedelta(minutes=schedule.every_minutes)
    assert schedule.time
    allowed = {
        "daily": set(range(7)),
        "weekdays": set(range(5)),
        "weekly": set(schedule.days) or {0},
    }[schedule.kind]
    for offset in range(8):
        candidate = _at(after + timedelta(days=offset), schedule.time)
        if candidate > after and candidate.weekday() in allowed:
            return candidate
    return None


def describe(schedule: Schedule) -> str:
    if schedule.kind == "once":
        if schedule.at is None:
            return "once"
        return "once, " + datetime.fromtimestamp(schedule.at).astimezone().strftime("%a %d %b at %H:%M")
    if schedule.kind == "interval":
        minutes = schedule.every_minutes or 0
        return (
            f"every {minutes // 60} hours"
            if minutes % 60 == 0 and minutes >= 60
            else f"every {minutes} minutes"
        )
    days = {"daily": "every day", "weekdays": "every weekday"}.get(schedule.kind)
    if days is None:
        days = "every " + ", ".join(DAYS[d].capitalize() for d in sorted(schedule.days or (0,)))
    return f"{days} at {schedule.time}"


def sentence(schedule: Schedule) -> str:
    """describe() for the start of a sentence (str.capitalize would lowercase 'Mon 26 Oct')."""
    text = describe(schedule)
    return text[:1].upper() + text[1:]


# ---- phrases -------------------------------------------------------------------------

TIME = r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)?|(?P<named>noon|midnight)"


def parse_time(match: re.Match[str]) -> str | None:
    if match.group("named"):
        return "12:00" if match.group("named") == "noon" else "00:00"
    hour, minute = int(match.group("h")), int(match.group("m") or 0)
    ampm = (match.group("ampm") or "").replace(".", "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_recurring(when: str) -> Schedule | None:
    """'every weekday at 8am', 'every monday and friday at 9:30', 'every 2 hours', 'daily at noon'."""
    when = when.lower().strip()
    m = re.fullmatch(r"every (?P<n>\d+) (?P<unit>minutes?|hours?)", when)
    if m:
        minutes = int(m.group("n")) * (60 if m.group("unit").startswith("hour") else 1)
        return Schedule(kind="interval", every_minutes=minutes) if minutes >= 5 else None
    m = re.fullmatch(rf"(?:every (?P<days>[a-z ,]+?)|(?P<daily>daily)) at (?:{TIME})", when)
    if not m:
        return None
    time = parse_time(m)
    if time is None:
        return None
    if m.group("daily"):
        return Schedule(kind="daily", time=time)
    days_text = m.group("days").strip()
    if days_text in ("day", "morning", "evening", "night"):
        return Schedule(kind="daily", time=time)
    if days_text in ("weekday", "weekdays", "work day", "workday"):
        return Schedule(kind="weekdays", time=time)
    names = [d for d in re.split(r",|\band\b|\s+", days_text) if d]
    days = [DAY_ALIASES.get(n.rstrip("s")) for n in names]
    if not days or any(d is None for d in days):
        return None
    return Schedule(kind="weekly", time=time, days=tuple(sorted({d for d in days if d is not None})))


def parse_once(when: str, now: datetime) -> Schedule | None:
    """'at 5pm', 'tomorrow at 9', 'in 20 minutes', 'in 2 hours'."""
    when = when.lower().strip()
    m = re.fullmatch(r"in (?P<n>\d+) (?P<unit>minutes?|hours?)", when)
    if m:
        delta = timedelta(minutes=int(m.group("n")) * (60 if m.group("unit").startswith("hour") else 1))
        return Schedule(kind="once", at=(now + delta).timestamp())
    m = re.fullmatch(rf"(?P<tomorrow>tomorrow )?at (?:{TIME})", when)
    if not m or (time := parse_time(m)) is None:
        return None
    target = _at(now, time)
    if m.group("tomorrow"):
        target += timedelta(days=1)
    elif target <= now:
        target += timedelta(days=1)  # "at 8am" said at 9am means tomorrow
    return Schedule(kind="once", at=target.timestamp())
