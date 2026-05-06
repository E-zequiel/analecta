import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager


class EventBus:
    """Fan-out SSE event bus: each subscriber gets its own asyncio.Queue."""

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[dict[str, object]]] = []

    @asynccontextmanager
    async def subscribe(
        self,
    ) -> AsyncGenerator["asyncio.Queue[dict[str, object]]", None]:
        """Yield a dedicated per-subscriber queue, removed automatically on exit.

        Yields:
            asyncio.Queue that receives all events published while subscribed.
        """
        q: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self._queues.append(q)
        try:
            yield q
        finally:
            self._queues.remove(q)

    async def publish(self, event: dict[str, object]) -> None:
        """Publish an event to every current subscriber.

        Args:
            event: Event payload to fan out.
        """
        for q in self._queues:
            await q.put(event)

    def put_nowait(self, event: dict[str, object]) -> None:
        """Sync publish — non-blocking; silently drops on full queue.

        Args:
            event: Event payload to fan out.
        """
        for q in self._queues:
            q.put_nowait(event)
