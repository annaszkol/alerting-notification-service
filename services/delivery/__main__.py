"""Delivery worker process (owner B, spec §6). Run as `python -m services.delivery`.

Env: DATABASE_URL, PUBLIC_BASE_URL (links in messages), DELIVERY_POLL_SECONDS (idle poll, default 0.5),
DELIVERY_BATCH_SIZE (default 50). Scale by running more instances: rows are claimed with SKIP LOCKED.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time

import psycopg

from libs import metrics
from services.delivery.worker import BATCH_SIZE, DeliveryWorker, refresh_queue

log = logging.getLogger("delivery")

CHANNEL_RELOAD_SECONDS = 30
QUEUE_METRIC_SECONDS = 5


async def run(stop: asyncio.Event) -> None:
    conninfo = os.environ["DATABASE_URL"]
    base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:3000")
    poll = float(os.environ.get("DELIVERY_POLL_SECONDS", "0.5"))
    batch = int(os.environ.get("DELIVERY_BATCH_SIZE", str(BATCH_SIZE)))

    while not stop.is_set():
        try:
            async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
                worker = DeliveryWorker(conn, base_url, batch_size=batch)
                await worker.load_adapters()
                loaded_at = time.monotonic()
                queue_at = 0.0
                log.info("delivery worker started, channels: %s", sorted(worker.adapters))
                while not stop.is_set():
                    if time.monotonic() - loaded_at > CHANNEL_RELOAD_SECONDS:
                        await worker.load_adapters()
                        loaded_at = time.monotonic()
                    if time.monotonic() - queue_at >= QUEUE_METRIC_SECONDS:
                        await refresh_queue(conn)
                        queue_at = time.monotonic()
                    if await worker.run_once() < batch:
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

    metrics.serve("delivery")

    async def runner() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(stop)
        log.info("delivery worker stopped")

    asyncio.run(runner())


if __name__ == "__main__":
    main()
