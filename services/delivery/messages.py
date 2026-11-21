"""Short texts for SMS and phone (spec §6 table). Full title/body rendering lands with the delivery worker."""

from __future__ import annotations

SMS_MAX = 160
ELLIPSIS = "…"


def sms_text(severity: str, title: str, alert_url: str, reply_code: str | None) -> str:
    """`[CRITICAL] <title> <url> ACK <code>`, at most 160 characters; only the title is shortened."""
    prefix = f"[{severity.upper()}] "
    suffix = f" {alert_url}" + (f" ACK {reply_code}" if reply_code else "")
    room = SMS_MAX - len(prefix) - len(suffix)
    title = " ".join(title.split())  # newlines and runs of spaces cost characters
    if room <= 0:
        return (prefix + suffix.strip())[:SMS_MAX]
    if len(title) > room:
        title = title[: room - len(ELLIPSIS)].rstrip() + ELLIPSIS
    return prefix + title + suffix


def phone_text(severity: str, title: str, can_ack: bool = True) -> str:
    text = f"{severity.capitalize()} alert. {' '.join(title.split())}."
    return text + (" Press 1 to acknowledge." if can_ack else "")
