"""Executor + scheduler process (owner B, spec §5, §7). Run as `python -m services.executor`.

Env: DATABASE_URL, ACTION_HTTP_ALLOWLIST (comma-separated hosts for http actions), EXECUTOR_POLL_SECONDS (0.5).
Scale by running more instances: step runs are claimed with SKIP LOCKED, scheduler jobs are idempotent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from datetime import UTC, datetime

import psycopg

from services.executor import scheduler
from services.executor.actions import REGISTRY
from services.executor.engine import Executor

log = logging.getLogger("executor")


async def run(stop: asyncio.Event) -> None:
    conninfo = os.environ["DATABASE_URL"]
    poll = float(os.environ.get("EXECUTOR_POLL_SECONDS", "0.5"))
    allowlist = frozenset(h.strip() for h in os.environ.get("ACTION_HTTP_ALLOWLIST", "").split(",") if h.strip())
    executor = Executor(actions=REGISTRY, http_allowlist=allowlist)

    while not stop.is_set():
        try:
            with psycopg.connect(conninfo, autocommit=True) as conn:
                log.info("executor started, actions: %s", sorted(REGISTRY))
                last_jobs = 0.0
                while not stop.is_set():
                    if time.monotonic() - last_jobs >= scheduler.INTERVAL_SECONDS:
                        done = scheduler.run_jobs(conn, datetime.now(UTC))
                        if any(done.values()):
                            log.info("scheduler: %s", done)
                        last_jobs = time.monotonic()
                    rows = executor.claim(conn, datetime.now(UTC))
                    for row in rows:
                        outcome = await executor.process(conn, row, datetime.now(UTC))
                        log.info("step run %s (%s/%s): %s", row["id"], row["run_id"], row["step_id"], outcome)
                    if not rows:
                        await _sleep(stop, poll)
        except (psycopg.Error, OSError) as exc:
            log.error("database error, reconnecting in 2 s: %s", exc)
            await _sleep(stop, 2)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    async def runner() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(stop)
        log.info("executor stopped")

    asyncio.run(runner())


if __name__ == "__main__":
    main()
