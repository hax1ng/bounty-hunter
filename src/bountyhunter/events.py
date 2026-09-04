"""Small in-process event bus used by the Organizer and NiceGUI logger."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from bountyhunter.models import HuntEvent

EventListener = Callable[[HuntEvent], None | Awaitable[None]]


class EventBus:
    """Fan events out without coupling background agents to GUI widgets."""

    def __init__(self) -> None:
        self._listeners: list[EventListener] = []

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def clear(self) -> None:
        self._listeners.clear()

    def emit(self, event: HuntEvent) -> None:
        """Publish to synchronous listeners.

        Awaitable listeners are scheduled on the active loop so a logger can
        never hold up the Organizer graph.
        """
        import asyncio

        for listener in list(self._listeners):
            try:
                result = listener(event)
                if inspect.isawaitable(result):
                    try:
                        asyncio.create_task(result)
                    except RuntimeError:
                        result.close()  # type: ignore[attr-defined]
            except Exception:
                # A stale/disconnected GUI listener must never stop the graph.
                continue

    async def publish(self, event: HuntEvent) -> None:
        """Publish from async agent code while keeping listeners non-blocking."""
        self.emit(event)
