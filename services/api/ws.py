"""WebSocket push (spec §2 point 5, contracts/ws-messages.md, contracts/notify.md).

Each API instance holds ONE `LISTEN alerts_changed` connection. Ids from notifications are collected for a
short window, the rows are read once by id (current state, never the payload), and every connected client
whose filters match gets `alert.upsert`. NOTIFY arrives only after commit, so a rolled-back change is never
pushed. If the listener connection drops, it reconnects with backoff and sends `resync`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import psycopg
from fastapi import WebSocket, WebSocketDisconnect
from psycopg_pool import ConnectionPool

from contracts.notify import ALERTS_CHANGED, parse_alerts_changed
from services.api.auth import Principal, decode_token, load_principal
from services.api.domain.alerts import DEFAULT_STATES, get_rows, summary
from services.api.errors import ApiError

log = logging.getLogger("api.ws")

CLOSE_UNAUTHORIZED = 4401
CLOSE_NO_PONG = 4408
SEVERITIES = {"info", "warning", "error", "critical"}
STATES = {"open", "acknowledged", "resolved"}


@dataclass
class Filters:
    team: str | None = None
    user: str | None = None
    severity: list[str] | None = None
    state: list[str] = field(default_factory=lambda: list(DEFAULT_STATES))

    @classmethod
    def parse(cls, raw: Any) -> Filters | None:
        """Filters from a `subscribe` message; None when malformed (the message is ignored)."""
        if raw is None:
            return cls()
        if not isinstance(raw, dict) or set(raw) - {"team", "user", "severity", "state"}:
            return None
        team, user = raw.get("team"), raw.get("user")
        severity, state = raw.get("severity"), raw.get("state")
        if any(v is not None and not isinstance(v, str) for v in (team, user)):
            return None
        for values, allowed in ((severity, SEVERITIES), (state, STATES)):
            if values is not None and (not isinstance(values, list) or not set(values) <= allowed):
                return None
        return cls(team=team, user=user, severity=severity, state=state or list(DEFAULT_STATES))

    def as_message(self) -> dict[str, Any]:
        out: dict[str, Any] = {"state": self.state}
        if self.team:
            out["team"] = self.team
        if self.user:
            out["user"] = self.user
        if self.severity:
            out["severity"] = self.severity
        return out

    def wants(self, alert: dict[str, Any]) -> bool:
        """Server-side filter: team, user, severity. State is applied by the client (state changes must reach
        it so it can drop an alert that left its `state` filter)."""
        if self.team and self.team not in alert["teams"]:
            return False
        if self.user and alert["owner"].get("user") != self.user:
            return False
        return not (self.severity and alert["severity"] not in self.severity)


class Client:
    def __init__(self, ws: WebSocket, user: Principal, queue_size: int) -> None:
        self.ws = ws
        self.user = user
        self.filters = Filters()
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self.missed_pongs = 0

    def offer(self, message: dict[str, Any]) -> None:
        """Never blocks the fan-out: a client that cannot keep up gets one `resync` instead of the backlog."""
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait({"type": "resync", "reason": "slow_consumer"})


class Hub:
    def __init__(
        self,
        database_url: str,
        pool: ConnectionPool,
        base_url: str,
        *,
        batch_window: float = 0.1,
        ping_interval: float = 20.0,
        queue_size: int = 2000,
    ) -> None:
        self.database_url = database_url
        self.pool = pool
        self.base_url = base_url
        self.batch_window = batch_window
        self.ping_interval = ping_interval
        self.queue_size = queue_size
        self.clients: set[Client] = set()
        self.listening = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # --- lifecycle -------------------------------------------------------------------------------------

    async def start(self, ready_timeout: float = 10.0) -> None:
        self._task = asyncio.create_task(self._listen_forever(), name="ws-listener")
        try:
            await asyncio.wait_for(self.listening.wait(), ready_timeout)
        except TimeoutError:
            log.warning("LISTEN not ready after %.0fs; continuing, it keeps retrying", ready_timeout)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _listen_forever(self) -> None:
        backoff = 0.5
        first = True
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(self.database_url, autocommit=True) as conn:
                    await conn.execute(f"LISTEN {ALERTS_CHANGED}")
                    self.listening.set()
                    if not first:
                        self.broadcast({"type": "resync", "reason": "listener_reconnected"})
                    first, backoff = False, 0.5
                    await self._consume(conn)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - any DB error: reconnect
                log.exception("LISTEN connection lost; reconnecting in %.1fs", backoff)
            self.listening.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10.0)

    async def _consume(self, conn: psycopg.AsyncConnection) -> None:
        # One task iterates notifies() without interruption: cancelling a pending anext() on that generator
        # (e.g. with wait_for) closes it and loses notifications. Waiting on the queue is safe to time out.
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def pump() -> None:
            async for notify in conn.notifies():
                queue.put_nowait(notify.payload)
            raise ConnectionError("notifies() ended")

        reader = asyncio.create_task(pump(), name="ws-listener-pump")
        loop = asyncio.get_running_loop()
        try:
            while True:
                ids = set(parse_alerts_changed(await self._next(queue, reader)))
                # collect everything that arrives within the batch window, then read the rows once
                deadline = loop.time() + self.batch_window
                while (remaining := deadline - loop.time()) > 0:
                    try:
                        ids.update(parse_alerts_changed(await asyncio.wait_for(queue.get(), remaining)))
                    except TimeoutError:
                        break
                if ids:
                    await self.push(sorted(ids))
        finally:
            reader.cancel()
            with contextlib.suppress(BaseException):
                await reader

    @staticmethod
    async def _next(queue: asyncio.Queue[str], reader: asyncio.Task[None]) -> str:
        """Next payload, or the reader's error when the connection is gone."""
        getter = asyncio.create_task(queue.get())
        await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
        if getter.done():
            return getter.result()
        getter.cancel()
        reader.result()  # raises the connection error
        raise ConnectionError("listener stopped")

    # --- fan-out ---------------------------------------------------------------------------------------

    def _read(self, ids: list[int]) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return [summary(row, self.base_url) for row in get_rows(conn, ids)]

    async def push(self, ids: list[int]) -> None:
        if not self.clients:
            return
        alerts = await asyncio.to_thread(self._read, ids)
        for alert in alerts:
            message = {"type": "alert.upsert", "alert": alert}
            for client in list(self.clients):
                if client.filters.wants(alert):
                    client.offer(message)

    def broadcast(self, message: dict[str, Any]) -> None:
        for client in list(self.clients):
            client.offer(message)

    # --- one connection --------------------------------------------------------------------------------

    def _authenticate(self, settings: Any, token: str | None) -> Principal | None:
        if not token:
            return None
        try:
            user_id = decode_token(settings, token)
        except ApiError:
            return None
        with self.pool.connection() as conn:
            return load_principal(conn, user_id)

    async def serve(self, ws: WebSocket, settings: Any) -> None:
        await ws.accept()
        user = await asyncio.to_thread(self._authenticate, settings, ws.query_params.get("token"))
        if user is None:
            await ws.close(code=CLOSE_UNAUTHORIZED, reason="invalid or expired token")
            return
        client = Client(ws, user, self.queue_size)
        self.clients.add(client)
        # Reading runs in this task; writer and pinger are helpers that are only cancelled, never awaited,
        # so shutdown does not wait on anything while the server cancels the connection.
        helpers = [asyncio.create_task(self._writer(client)), asyncio.create_task(self._pinger(client))]
        try:
            await self._reader(client)
        finally:
            self.clients.discard(client)
            for task in helpers:
                task.cancel()

    async def _reader(self, client: Client) -> None:
        while True:
            try:
                text = await client.ws.receive_text()
            except (WebSocketDisconnect, RuntimeError):  # RuntimeError: we closed it (no pong)
                return
            try:
                message = json.loads(text)
            except ValueError:
                log.info("ignoring non-JSON message from %s", client.user.id)
                continue
            kind = message.get("type") if isinstance(message, dict) else None
            if kind == "pong":
                client.missed_pongs = 0
            elif kind == "subscribe" and (filters := Filters.parse(message.get("filters"))) is not None:
                client.filters = filters
                client.offer({"type": "subscribed", "filters": filters.as_message()})
            else:
                log.info("ignoring message from %s: %.200s", client.user.id, text)

    async def _writer(self, client: Client) -> None:
        try:
            while True:
                await client.ws.send_json(await client.queue.get())
        except (WebSocketDisconnect, RuntimeError, OSError):
            return  # connection gone; the reader ends the session

    async def _pinger(self, client: Client) -> None:
        while True:
            await asyncio.sleep(self.ping_interval)
            if client.missed_pongs >= 2:
                log.info("closing %s: no pong", client.user.id)
                with contextlib.suppress(Exception):
                    await client.ws.close(code=CLOSE_NO_PONG)
                return
            client.missed_pongs += 1
            client.offer({"type": "ping"})
