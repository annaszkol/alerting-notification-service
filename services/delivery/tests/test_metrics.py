"""Notification queue gauge (spec §10 point 3)."""

import asyncio

from prometheus_client import REGISTRY

from services.delivery.worker import refresh_queue


class Conn:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, sql):
        rows = self.rows

        class Cursor:
            async def fetchall(self):
                return rows

        return Cursor()


def gauge(status):
    return REGISTRY.get_sample_value("notifications_queue", {"status": status})


def test_queue_gauge_by_status_and_missing_statuses_reset_to_zero():
    asyncio.run(refresh_queue(Conn([("pending", 7), ("dead", 1)])))
    assert (gauge("pending"), gauge("dead"), gauge("sent")) == (7, 1, 0)
    asyncio.run(refresh_queue(Conn([("sent", 8)])))
    assert (gauge("pending"), gauge("dead"), gauge("sent")) == (0, 0, 8)
