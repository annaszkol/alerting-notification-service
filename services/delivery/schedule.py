"""Pure time logic for routing (spec §4 Routing, §6 Dyżury): channel schedules, preferences, on-call rotations.

All schedule windows are evaluated in the RECIPIENT's time zone; rotations in the rotation's own time zone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from contracts.delivery import Channel, Severity

SEVERITY_ORDER: tuple[Severity, ...] = (Severity.INFO, Severity.WARNING, Severity.ERROR, Severity.CRITICAL)

# Used when neither the person nor any of their teams has preferences.
DEFAULT_PREFERENCES: Mapping[str, Any] = {
    "info": ["ui"],
    "warning": ["ui"],
    "error": ["email"],
    "critical": ["sms", "email"],
}


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def in_window(between: str, days: Sequence[int] | None, local: datetime) -> bool:
    """`between` is "HH:MM-HH:MM", end exclusive, may wrap midnight. `days` are ISO weekdays of the day the
    window STARTS (so "22:00-06:00" on Friday covers Saturday 03:00)."""
    start_s, end_s = between.split("-")
    start, end, now = _hhmm(start_s), _hhmm(end_s), local.time().replace(second=0, microsecond=0)
    today, yesterday = local.isoweekday(), (local - timedelta(days=1)).isoweekday()
    allowed = set(days) if days else set(range(1, 8))
    if start == end:  # whole day
        return today in allowed
    if start < end:
        return start <= now < end and today in allowed
    return (now >= start and today in allowed) or (now < end and yesterday in allowed)


def pick_channels(spec: Any, local: datetime) -> list[Channel]:
    """A channel list, or {schedule: [{between, days?, use}], otherwise} (contracts/route.schema.json)."""
    if isinstance(spec, list):
        return [Channel(c) for c in spec]
    if isinstance(spec, Mapping) and "schedule" in spec:
        for window in spec["schedule"]:
            if in_window(window["between"], window.get("days"), local):
                return [Channel(c) for c in window["use"]]
        return [Channel(c) for c in spec.get("otherwise", [])]
    return []


def preference_for(by_severity: Mapping[str, Any], severity: Severity | str) -> Any | None:
    """Preference entry for a severity; a missing level falls back to the nearest LOWER level that is set."""
    index = SEVERITY_ORDER.index(Severity(severity))
    for level in reversed(SEVERITY_ORDER[: index + 1]):
        if level.value in by_severity:
            return by_severity[level.value]
    return None


@dataclass(frozen=True)
class Rotation:
    """Ordered list of people; each shift lasts `shift_length` and changes at `handoff_time` local time.
    The first shift (rotation[0]) starts on `starts_on` at `handoff_time`."""

    team_id: str
    users: tuple[str, ...]
    shift_length: timedelta
    handoff_time: time
    timezone: str
    starts_on: date

    def on_call(self, now: datetime) -> str | None:
        if not self.users or self.shift_length <= timedelta(0):
            return None
        tz = zone(self.timezone)
        anchor = datetime.combine(self.starts_on, self.handoff_time, tzinfo=tz)
        # Wall-clock arithmetic in the rotation's zone, so a 1-day shift always changes at handoff_time,
        # also across DST changes.
        local = now.astimezone(tz).replace(tzinfo=None)
        elapsed = local - anchor.replace(tzinfo=None)
        shift = elapsed // self.shift_length
        return self.users[shift % len(self.users)]
