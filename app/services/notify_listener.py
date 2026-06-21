"""
PostgreSQL LISTEN/NOTIFY listener — cross-process event sync.
Each process (bot, web) runs this independently to receive events
emitted by the other process via pg_notify().

Usage:
    listener = PGNotifyListener(database_url, handler_fn)
    await listener.start()  # runs forever, reconnects on drop
"""
import asyncio
import json
import logging
from typing import Callable, Awaitable

import asyncpg

logger = logging.getLogger(__name__)

CHANNEL = "tournament_os_events"
Handler = Callable[[dict], Awaitable[None]]


class PGNotifyListener:
    def __init__(self, database_url: str, handler: Handler):
        # asyncpg uses plain postgresql:// not sqlalchemy's postgresql+asyncpg://
        self._dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self._handler = handler
        self._conn: asyncpg.Connection | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.warning("LISTEN/NOTIFY disconnected: %s — reconnecting in 5s", e)
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
        if self._conn:
            await self._conn.close()

    async def _connect_and_listen(self) -> None:
        self._conn = await asyncpg.connect(dsn=self._dsn)
        await self._conn.add_listener(CHANNEL, self._on_notify)
        logger.info("PGNotifyListener: listening on channel '%s'", CHANNEL)
        # Keep alive — poll every 30s as a safety net
        while self._running:
            await asyncio.sleep(30)
            # Lightweight heartbeat query
            try:
                await self._conn.fetchval("SELECT 1")
            except Exception:
                break

    async def _on_notify(self, connection, pid, channel, payload) -> None:
        try:
            data = json.loads(payload)
            await self._handler(data)
        except Exception as e:
            logger.error("NOTIFY handler error: %s (payload=%r)", e, payload)
