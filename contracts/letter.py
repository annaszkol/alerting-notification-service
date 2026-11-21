"""Letter contract (owner A, spec §3, §5, §6, §13).

A `Letter` is the canonical flat view of one event, produced by the processor after
schema mapping. It is what conditions match against, what templates render from, and
what ends up stored on the alert body. A new schema produces a new `Letter` - the
processor and the alert model never change.

Reserved fields (always present, always `$`-prefixed):

    $schema        id of the schema that produced this Letter
    $source_id     source id from the envelope
    $event_id      envelope event id (stable across replays)
    $received_at   ISO-8601 UTC of ingest
    $split_index   zero-based index when `schema.splitOn` produced multiple Letters
                   (absent when the event was not split)

All other top-level fields come from `schema.mapping` or from the raw payload when
the mapping copies a subtree. Live fields used at display/send time (`$count`,
`$state`, `$alert_url`) are not part of this Letter - they are added by the delivery
layer (`contracts/delivery.md`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Letter:
    """One canonical event.

    `fields` holds everything a condition or template can address with `$<name>`.
    `raw` is the parsed payload, addressable as `payload.<path>`.
    """

    fields: dict[str, Any] = field(default_factory=dict)
    raw: Any = None
    source_id: str = ""
    event_id: str = ""
    received_at: str = ""  # ISO-8601 UTC
    schema: str = ""
    split_index: int | None = None

    def view(self) -> dict[str, Any]:
        """The mapping a condition or template evaluates against.

        Top-level names (no `$`) resolve from `fields`; `payload` resolves to `raw`.
        Reserved `$` fields are injected here so conditions and templates can
        reference them without special-casing.
        """
        v: dict[str, Any] = dict(self.fields)
        v["payload"] = self.raw
        v["$schema"] = self.schema
        v["$source_id"] = self.source_id
        v["$event_id"] = self.event_id
        v["$received_at"] = self.received_at
        if self.split_index is not None:
            v["$split_index"] = self.split_index
        return v
