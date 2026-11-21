"""Runbook executor state machine (spec §7, contracts/runbooks.md).

A run walks the runbook's steps in order; each visited step gets a `step_runs` row (`seq` = visit order).

- `text`  manual step: created `pending` with no `next_run_at`; does NOT block the run (a checklist can be
          ticked in any order). `mark_done` records who ticked it and when. The run finishes once it walked past
          its last step and no step is open.
- `wait`  created `scheduled` with `next_run_at = now + duration`; the executor completes it when due.
- `action` mode `auto`: due at once. Mode `manual`: waits for a person, the "run" button (`run_step`) makes it
          due. When it comes up and its effective approval is `required` (the runbook can raise the action's
          minimum, never lower it) it goes `waiting_approval` and the approval request is sent at once; it runs
          after `approve`, `reject` ends the run. Executed up to `attempts` times (RETRY_DELAY apart); on final
          failure the run jumps to `on_failure` or, without it, records the failure and continues (spec §7). `resolves`
          resolves the alert and ends the run on success; `only_if_open` skips the step when the alert is
          already resolved.

The executor claims due rows with a lease (`FOR UPDATE SKIP LOCKED`), runs the action outside any transaction,
then stores the result only if the lease token still matches, so a crashed or slow executor never overwrites
the result of the one that took over. State lives in Postgres: a restart continues where it stopped.

Everything here is synchronous SQL on the caller's connection and transaction, so the API can call
`start_run`, `run_step`, `mark_done`, `approve`, `reject`, `cancel_run` inside its own request transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.actions import Action, ActionContext, ActionResult, Approval, effective_approval
from db.rows import plain_rows
from services.executor.alerts import AlertGateway, SqlAlerts
from services.executor.templating import render_params

LEASE = timedelta(seconds=60)
RETRY_DELAY = timedelta(seconds=5)
MAX_STEP_RUNS = 100  # protects against on_failure loops
ACTIVE_RUN = ("running", "waiting_approval")


_UNITS = {"s": "seconds", "m": "minutes", "h": "hours"}


def parse_duration(value: str) -> timedelta:
    """Wait step duration: "30s", "2m", "1h" (contracts/runbook.schema.json)."""
    value = str(value)
    if value[-1:] not in _UNITS or not value[:-1].isdigit():
        raise ValueError(f"bad duration {value!r}")
    return timedelta(**{_UNITS[value[-1]]: int(value[:-1])})


class ExecutorError(Exception):
    """A request that does not fit the current state (e.g. approving a step that is not waiting)."""


@dataclass(frozen=True)
class Hooks:
    """Side effects owned by other branches; the defaults do nothing.

    approval_requested(conn, step_run_id, run, step): feat/actions-approvals creates the `approvals` row with its
    code and sends `kind = approval` notifications, in the same transaction.
    """

    approval_requested: Callable[[psycopg.Connection, int, Mapping[str, Any], Mapping[str, Any]], None] = (
        lambda conn, step_run_id, run, step: None
    )


# --- helpers ---------------------------------------------------------------------------------------------


def _steps(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(run["runbook"].get("steps", []))


def _step(run: Mapping[str, Any], step_id: str) -> dict[str, Any]:
    return next(s for s in _steps(run) if s["id"] == step_id)


def _next_step_id(run: Mapping[str, Any], after: str | None) -> str | None:
    ids = [s["id"] for s in _steps(run)]
    if after is None:
        return ids[0] if ids else None
    index = ids.index(after) + 1
    return ids[index] if index < len(ids) else None


def _effective(step: Mapping[str, Any], actions: Mapping[str, Action]) -> Approval:
    action = actions.get(step.get("action", ""))
    minimum = action.min_approval if action else Approval.REQUIRED  # unknown action: never auto-run
    return effective_approval(Approval(step.get("approval", "none")), minimum)


def _get_run(conn: psycopg.Connection, run_id: int, lock: bool = False) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT * FROM runbook_runs WHERE id = %s{' FOR UPDATE' if lock else ''}", (run_id,))
        run = cur.fetchone()
    if run is None:
        raise ExecutorError(f"run {run_id} not found")
    return run


def _get_step_run(conn: psycopg.Connection, step_run_id: int) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM step_runs WHERE id = %s FOR UPDATE", (step_run_id,))
        row = cur.fetchone()
    if row is None:
        raise ExecutorError(f"step run {step_run_id} not found")
    return row


def _finish_run(conn: psycopg.Connection, run_id: int, status: str, now: datetime) -> None:
    conn.execute(
        "UPDATE runbook_runs SET status = %s, finished_at = %s"
        " WHERE id = %s AND status IN ('running', 'waiting_approval')",
        (status, now, run_id),
    )


def _maybe_finish(conn: psycopg.Connection, run_id: int, now: datetime) -> None:
    """Finish a run that walked past its last step once no step is open (manual checklist items included).
    The run failed if its last automatic step failed."""
    open_steps = conn.execute(
        "SELECT count(*) FROM step_runs"
        " WHERE run_id = %s AND status NOT IN ('succeeded', 'failed', 'skipped', 'rejected', 'done')",
        (run_id,),
    ).fetchone()[0]
    reached_end = conn.execute("SELECT reached_end FROM runbook_runs WHERE id = %s", (run_id,)).fetchone()[0]
    if open_steps or not reached_end:
        return
    last = conn.execute(
        "SELECT status FROM step_runs WHERE run_id = %s AND kind <> 'text' ORDER BY seq DESC LIMIT 1", (run_id,)
    ).fetchone()
    _finish_run(conn, run_id, "failed" if last and last[0] == "failed" else "succeeded", now)


# --- walking the runbook ---------------------------------------------------------------------------------


@dataclass
class Executor:
    actions: Mapping[str, Action]
    alerts: AlertGateway = field(default_factory=SqlAlerts)
    hooks: Hooks = field(default_factory=Hooks)
    http_allowlist: frozenset[str] = frozenset()

    def advance(self, conn: psycopg.Connection, run_id: int, step_id: str | None, now: datetime) -> None:
        """Create step runs from `step_id` on, until one has to wait (action / wait) or the runbook ends."""
        run = _get_run(conn, run_id, lock=True)
        while True:
            if run["status"] not in ACTIVE_RUN:
                return
            if step_id is None:
                conn.execute("UPDATE runbook_runs SET reached_end = true WHERE id = %s", (run_id,))
                _maybe_finish(conn, run_id, now)
                return
            seq = conn.execute(
                "SELECT coalesce(max(seq), 0) + 1 FROM step_runs WHERE run_id = %s", (run_id,)
            ).fetchone()[0]
            if seq > MAX_STEP_RUNS:
                _finish_run(conn, run_id, "failed", now)
                return
            step = _step(run, step_id)
            kind = step["kind"]
            if kind == "text":
                status, next_run_at = "pending", None
            elif kind == "wait":
                status, next_run_at = "scheduled", now + parse_duration(step["duration"])
            else:  # auto: due now (an approval-gated step then asks for approval at once); manual: "run" button
                status, next_run_at = "pending", (now if run["mode"] == "auto" else None)
            conn.execute(
                """INSERT INTO step_runs (run_id, seq, step_id, kind, status, next_run_at, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (run_id, seq, step_id, kind, status, next_run_at, now),
            )
            if kind != "text":
                return  # the executor (or a person) continues from here
            step_id = _next_step_id(run, step_id)

    @plain_rows
    def start_run(
        self,
        conn: psycopg.Connection,
        alert_id: int,
        runbook: Mapping[str, Any],
        now: datetime,
        started_by: str | None = None,
        mode: str | None = None,
    ) -> int:
        """Start a run of a (manager-approved) canonical runbook. The caller checks permissions and approval."""
        run_id = conn.execute(
            """INSERT INTO runbook_runs (alert_id, runbook_id, runbook, mode, started_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (alert_id, runbook["id"], Jsonb(dict(runbook)), mode or runbook.get("mode", "manual"), started_by, now),
        ).fetchone()[0]
        self.advance(conn, run_id, _next_step_id({"runbook": runbook}, None), now)
        return run_id

    # --- people ------------------------------------------------------------------------------------------

    @plain_rows
    def run_step(self, conn: psycopg.Connection, step_run_id: int, user: str, now: datetime) -> None:
        """The "run" button for an action step waiting for a person."""
        row = _get_step_run(conn, step_run_id)
        if row["kind"] != "action" or row["status"] != "pending" or row["next_run_at"] is not None:
            raise ExecutorError(f"step run {step_run_id} is not waiting to be started")
        conn.execute("UPDATE step_runs SET next_run_at = %s, started_by = %s WHERE id = %s", (now, user, step_run_id))

    @plain_rows
    def mark_done(self, conn: psycopg.Connection, step_run_id: int, user: str, now: datetime) -> None:
        """Tick a manual (text) step: records who and when (spec §13 test 13)."""
        row = _get_step_run(conn, step_run_id)
        if row["kind"] != "text" or row["status"] != "pending":
            raise ExecutorError(f"step run {step_run_id} is not an open manual step")
        conn.execute(
            "UPDATE step_runs SET status = 'done', executed_by = %s, finished_at = %s WHERE id = %s",
            (user, now, step_run_id),
        )
        _maybe_finish(conn, row["run_id"], now)

    @plain_rows
    def approve(self, conn: psycopg.Connection, step_run_id: int, user: str, now: datetime) -> None:
        """State change only; the role check and the approval code live in feat/actions-approvals."""
        row = _get_step_run(conn, step_run_id)
        if row["status"] != "waiting_approval":
            raise ExecutorError(f"step run {step_run_id} is not waiting for approval")
        conn.execute(
            "UPDATE step_runs SET status = 'pending', approved_by = %s, next_run_at = %s WHERE id = %s",
            (user, now, step_run_id),
        )
        conn.execute(
            "UPDATE runbook_runs SET status = 'running' WHERE id = %s AND status = 'waiting_approval'",
            (row["run_id"],),
        )

    @plain_rows
    def reject(self, conn: psycopg.Connection, step_run_id: int, user: str, now: datetime) -> None:
        """Rejection ends the run (spec §7)."""
        row = _get_step_run(conn, step_run_id)
        if row["status"] != "waiting_approval":
            raise ExecutorError(f"step run {step_run_id} is not waiting for approval")
        conn.execute(
            "UPDATE step_runs SET status = 'rejected', approved_by = %s, finished_at = %s WHERE id = %s",
            (user, now, step_run_id),
        )
        _finish_run(conn, row["run_id"], "rejected", now)

    @plain_rows
    def cancel_run(self, conn: psycopg.Connection, run_id: int, now: datetime) -> None:
        _finish_run(conn, run_id, "cancelled", now)
        conn.execute(
            "UPDATE step_runs SET status = 'skipped', finished_at = %s, next_run_at = NULL"
            " WHERE run_id = %s AND status IN ('pending', 'scheduled', 'waiting_approval')",
            (now, run_id),
        )

    # --- the executor loop -------------------------------------------------------------------------------

    def claim(self, conn: psycopg.Connection, now: datetime, limit: int = 20) -> list[dict[str, Any]]:
        """Due step runs (and ones whose executor died), leased to this executor. Commits."""
        token = uuid.uuid4()
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE step_runs SET status = 'running', lease_until = %(lease_until)s, lease_token = %(token)s,
                                     started_at = coalesce(started_at, %(now)s)
                WHERE id IN (
                    SELECT id FROM step_runs
                    WHERE (status IN ('pending', 'scheduled') AND next_run_at <= %(now)s)
                       OR (status = 'running' AND lease_until < %(now)s)
                    ORDER BY next_run_at
                    LIMIT %(limit)s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                {"now": now, "lease_until": now + LEASE, "token": token, "limit": limit},
            )
            return sorted(cur.fetchall(), key=lambda r: r["id"])

    async def process(self, conn: psycopg.Connection, row: Mapping[str, Any], now: datetime) -> str:
        """Handle one claimed step run. Returns what happened (for logs and tests)."""
        with conn.transaction():
            run = _get_run(conn, row["run_id"])
            step = _step(run, row["step_id"])
            if run["status"] not in ACTIVE_RUN:
                self._close(conn, row, "skipped", now)
                return "skipped (run finished)"
            alert = self.alerts.get(conn, run["alert_id"])
            if step.get("only_if_open") and (alert is None or alert.state == "resolved"):
                if self._close(conn, row, "skipped", now):
                    self.advance(conn, run["id"], _next_step_id(run, step["id"]), now)
                return "skipped (alert resolved)"
            if row["kind"] == "wait":
                if self._close(conn, row, "succeeded", now):
                    self.advance(conn, run["id"], _next_step_id(run, step["id"]), now)
                return "waited"
            if _effective(step, self.actions) is Approval.REQUIRED and row["approved_by"] is None:
                parked = self._update(
                    conn, row, status="waiting_approval", next_run_at=None, lease_until=None, lease_token=None
                )
                if parked:
                    conn.execute("UPDATE runbook_runs SET status = 'waiting_approval' WHERE id = %s", (run["id"],))
                    self.hooks.approval_requested(conn, row["id"], run, step)
                return "waiting for approval"
            if alert is None:
                return self._fail(conn, run, step, row, "alert not found", now)
            attempt = row["attempt"] + 1
            params = render_params(step.get("params", {}), alert.letter)
            if not self._update(conn, row, attempt=attempt, params=Jsonb(params)):
                return "lost lease"

        action = self.actions.get(step["action"])
        if action is None:
            result = ActionResult(ok=False, error=f"unknown action {step['action']!r}")
        else:
            ctx = ActionContext(
                alert=alert,
                runbook_id=run["runbook_id"],
                runbook_run_id=run["id"],
                step_run_id=row["id"],
                step_id=step["id"],
                attempt=attempt,
                started_by=row["started_by"] or run["started_by"],
                approved_by=row["approved_by"],
                http_allowlist=self.http_allowlist,
            )
            try:
                result = await action.execute(params, ctx)
            except Exception as exc:  # an exception is a failed attempt (contracts/runbooks.md)
                result = ActionResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        with conn.transaction():
            row = {**row, "attempt": attempt}
            if result.ok:
                if not self._close(conn, row, "succeeded", now, result=Jsonb(result.output)):
                    return "lost lease"
                if step.get("resolves"):  # the problem is fixed: resolve the alert and end the run
                    by = row["started_by"] or run["started_by"]
                    self.alerts.resolve(conn, run["alert_id"], how="runbook", by=by, now=now)
                    conn.execute("UPDATE runbook_runs SET reached_end = true WHERE id = %s", (run["id"],))
                    _finish_run(conn, run["id"], "succeeded", now)
                    return "succeeded, alert resolved"
                self.advance(conn, run["id"], _next_step_id(run, step["id"]), now)
                return "succeeded"
            if attempt < int(step.get("attempts", 1)):
                updated = self._update(
                    conn, row, status="pending", next_run_at=now + RETRY_DELAY, error=result.error,
                    result=Jsonb(result.output), lease_until=None, lease_token=None,
                )  # fmt: skip
                return "retry" if updated else "lost lease"
            return self._fail(conn, run, step, row, result.error, now, output=result.output)

    def _fail(self, conn, run, step, row, error, now, output=None) -> str:
        if not self._close(conn, row, "failed", now, error=error, result=Jsonb(output or {})):
            return "lost lease"
        target = step.get("on_failure") or _next_step_id(run, step["id"])
        self.advance(conn, run["id"], target, now)
        return "failed"

    def _update(self, conn: psycopg.Connection, row: Mapping[str, Any], **values: Any) -> bool:
        """Update a claimed row only while our lease token is still on it."""
        sets = ", ".join(f"{column} = %({column})s" for column in values)
        cur = conn.execute(
            f"UPDATE step_runs SET {sets} WHERE id = %(id)s AND status = 'running' AND lease_token = %(token)s",
            {**values, "id": row["id"], "token": row["lease_token"]},
        )
        return cur.rowcount == 1

    def _close(
        self, conn: psycopg.Connection, row: Mapping[str, Any], status: str, now: datetime, **values: Any
    ) -> bool:
        return self._update(
            conn, row, status=status, finished_at=now, lease_until=None, lease_token=None, next_run_at=None, **values
        )
