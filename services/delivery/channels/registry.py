"""Channel adapter registry: `channels.adapter` -> class (contract: contracts/delivery.md)."""

from __future__ import annotations

from typing import Any

from contracts.delivery import ChannelAdapter

_ADAPTERS: dict[str, type] = {}


def register[T: type](cls: T) -> T:
    name = cls.adapter
    if name in _ADAPTERS:
        raise ValueError(f"channel adapter {name!r} registered twice")
    _ADAPTERS[name] = cls
    return cls


def create(adapter: str, config: dict[str, Any]) -> ChannelAdapter:
    _load_builtin()
    try:
        cls = _ADAPTERS[adapter]
    except KeyError:
        raise ValueError(f"unknown channel adapter {adapter!r}; known: {sorted(_ADAPTERS)}") from None
    return cls(config)


def names() -> list[str]:
    _load_builtin()
    return sorted(_ADAPTERS)


def _load_builtin() -> None:
    # Importing the modules registers their classes.
    from services.delivery.channels import email, http_gateway, ui  # noqa: F401
