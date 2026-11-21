"""Runbook action contract (owner B, spec §7).

A new action is one class implementing `Action` plus registration in the executor's registry.
The UI builds the step editor form from `params_schema`. Description: contracts/runbooks.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class Approval(StrEnum):
    NONE = "none"
    REQUIRED = "required"


def effective_approval(step: Approval, action_min: Approval) -> Approval:
    """A runbook may raise an action's approval level, never lower it (AGENTS.md invariant)."""
    return Approval.REQUIRED if Approval.REQUIRED in (step, action_min) else Approval.NONE


@dataclass(frozen=True)
class AlertContext:
    id: int
    state: str  # open / acknowledged / resolved
    owner: dict[str, str]
    severity: str
    letter: dict[str, Any]  # first event letter: $ fields + payload


@dataclass(frozen=True)
class ActionContext:
    alert: AlertContext
    runbook_id: str
    runbook_run_id: int
    step_run_id: int
    step_id: str
    attempt: int  # 1-based
    started_by: str | None  # user id; None when started by mode=auto
    approved_by: str | None
    http_allowlist: frozenset[str]  # hosts; checked AFTER template substitution (SSRF, spec §11)


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)  # shown on the alert timeline
    error: str | None = None


class Action(Protocol):
    name: str  # registry key, used as step.action
    params_schema: dict[str, Any]  # JSON Schema of params after template substitution
    min_approval: Approval

    async def execute(self, params: dict[str, Any], ctx: ActionContext) -> ActionResult:
        """Run once. Params are already rendered and validated against params_schema.

        Never raises for expected failures: return ActionResult(ok=False, error=...).
        The executor handles attempts, on_failure, resolves and only_if_open.
        Must be safe to repeat (the executor may re-run a step after a crash).
        """
        ...
