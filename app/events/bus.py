"""Event bus for publishing and subscribing to events (ArchitectureV2 §37-39)."""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from app.events.models import Event
from app.events.store import EventStore
from app.observability.logging import get_logger

logger = get_logger(__name__)

EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    """In-memory event bus with persistent storage and deduplication."""

    def __init__(self, store: EventStore):
        self.store = store
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._wildcard_handlers: list[EventHandler] = []
        self._processed_events: set[str] = set()  # For deduplication (§38)
        self._sequence_counters: dict[str, int] = defaultdict(int)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe to a specific event type."""
        self._handlers[event_type].append(handler)
        logger.debug("event_subscribed", event_type=event_type)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe to all events (wildcard)."""
        self._wildcard_handlers.append(handler)
        logger.debug("wildcard_subscribed", handler=handler.__name__)

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers with deduplication."""
        # Deduplication (§38): skip if already processed
        if event.event_id in self._processed_events:
            logger.debug("event_deduplicated", event_id=event.event_id)
            return

        # Persist first
        await self.store.append(event)
        self._processed_events.add(event.event_id)

        # Deliver to specific handlers
        handlers = self._handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("event_handler_failed",
                               event_type=event.event_type,
                               handler=handler.__name__)

        # Deliver to wildcard handlers
        for handler in self._wildcard_handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("wildcard_handler_failed", handler=handler.__name__)

    async def publish_and_wait(self, event: Event, timeout: float = 30.0) -> list[Any]:
        """Publish and wait for all handlers to complete, returning results."""
        await self.store.append(event)
        self._processed_events.add(event.event_id)

        all_handlers = self._handlers.get(event.event_type, []) + self._wildcard_handlers
        if not all_handlers:
            return []

        tasks = [asyncio.create_task(handler(event)) for handler in all_handlers]
        done, pending = await asyncio.wait(
            tasks,
            timeout=timeout,
            return_when=asyncio.ALL_COMPLETED
        )

        results = []
        for task in done:
            try:
                results.append(task.result())
            except Exception as exc:
                logger.exception("event_handler_failed_in_wait")
                results.append({"error": str(exc)})

        for task in pending:
            task.cancel()

        return results

    def next_sequence(self, source: str) -> int:
        """Get next sequence number for a source (§39)."""
        self._sequence_counters[source] += 1
        return self._sequence_counters[source]

    def create_event(
        self,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> Event:
        """Create an event with auto-assigned sequence number."""
        return Event(
            event_type=event_type,
            source=source,
            payload=payload,
            sequence_number=self.next_sequence(source),
            correlation_id=correlation_id,
        )
