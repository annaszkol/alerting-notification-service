"""Placeholder for the processor process (owner: A) - Kafka batch -> one Postgres transaction (spec §5).

Replaced by the real implementation on its feature branch. It only keeps the container running
so that `docker compose up` starts the full topology from day one.
"""

import logging
import signal
import threading

log = logging.getLogger("processor")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log.info("processor placeholder started (not implemented yet)")
    stop.wait()
    log.info("processor placeholder stopped")


if __name__ == "__main__":
    main()
