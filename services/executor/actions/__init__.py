"""Action registry (contracts/actions.py). Built-in actions are added on feat/actions-approvals."""

from __future__ import annotations

from contracts.actions import Action

REGISTRY: dict[str, Action] = {}


def register(action: Action) -> Action:
    if action.name in REGISTRY:
        raise ValueError(f"action {action.name!r} registered twice")
    REGISTRY[action.name] = action
    return action
