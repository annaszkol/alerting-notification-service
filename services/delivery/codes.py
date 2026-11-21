"""Reply codes people send back by SMS: `ACK <code>`, `APPROVE <code>`, `REJECT <code>` (contracts/delivery.md)."""

from __future__ import annotations

import secrets

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I: easy to read and type on a phone
CODE_LENGTH = 6


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
