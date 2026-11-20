"""Placeholder for the delivery process (owner: B) - outbox delivery workers (spec §6).

Replaced by the real implementation on its feature branch. It only keeps the container running
so that `docker compose up` starts the full topology from day one.
"""

import logging
import signal
import threading

log = logging.getLogger("delivery")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log.info("delivery placeholder started (not implemented yet)")
    stop.wait()
    log.info("delivery placeholder stopped")


if __name__ == "__main__":
    main()
