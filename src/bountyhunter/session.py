"""In-memory workspace session: open Target, GUI state, and event stream."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from bountyhunter.events import EventBus
from bountyhunter.models import (
    EventKind,
    EventLevel,
    GuiState,
    HuntEvent,
    HuntState,
    ModelProvider,
    Target,
)
from bountyhunter.providers import (
    AnthropicConnection,
    OpenAIConnection,
    load_anthropic_connection,
    load_openai_connection,
)
from bountyhunter.settings import AppSettings, load_settings, save_settings
from bountyhunter.store import HuntStore, load_target, save_target

# Compatibility names used by the first workspace implementation.
LogLevel = EventLevel
LogEvent = HuntEvent

Listener = Callable[[], None]
LogListener = Callable[[HuntEvent], None]


@dataclass
class Session:
    target: Target | None = None
    path: Path | None = None
    settings: AppSettings = field(default_factory=load_settings)
    settings_path: Path | None = None
    gui: GuiState = field(default_factory=GuiState)
    event_bus: EventBus = field(default_factory=EventBus)
    openai: OpenAIConnection = field(default_factory=load_openai_connection)
    anthropic: AnthropicConnection = field(default_factory=load_anthropic_connection)
    _snapshot: str = ""
    _listeners: list[Listener] = field(default_factory=list)
    events: list[HuntEvent] = field(default_factory=list)
    chat: list[tuple[str, str]] = field(default_factory=list)
    hunt_note: str = "Idle"

    def __post_init__(self) -> None:
        self.openai.use_subscription_first = self.settings.openai_subscription_first
        self.anthropic.use_subscription_first = self.settings.anthropic_subscription_first

    def connection_for(
        self, provider: ModelProvider
    ) -> OpenAIConnection | AnthropicConnection:
        """Return the credential/connection state for a role's provider."""
        if provider is ModelProvider.ANTHROPIC:
            return self.anthropic
        return self.openai

    @property
    def dirty(self) -> bool:
        return self.target is not None and self.target.fingerprint() != self._snapshot

    @property
    def has_target(self) -> bool:
        return self.target is not None

    @property
    def window_title(self) -> str:
        if self.target is None:
            return "Bounty Hunter"
        star = "*" if self.dirty else ""
        return f"Bounty Hunter — {self.target.name}{star}"

    def on_change(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def on_log(self, fn: LogListener) -> None:
        self.event_bus.subscribe(fn)

    def notify(self) -> None:
        for fn in list(self._listeners):
            fn()

    def log(
        self,
        message: str,
        *,
        source: str = "workspace",
        level: EventLevel = EventLevel.INFO,
        kind: EventKind | None = None,
        phase: HuntState | None = None,
        hunt_id: str | None = None,
        slice_id: str | None = None,
    ) -> HuntEvent:
        if kind is None:
            kind = {
                "file": EventKind.FILE,
                "safety": EventKind.SAFETY,
                "assistant": EventKind.CHAT,
            }.get(source, EventKind.STATUS)
        current_phase = phase or (
            self.target.hunt_state if self.target is not None else HuntState.IDLE
        )
        event = HuntEvent(
            hunt_id=hunt_id if hunt_id is not None else self.gui.hunt_id,
            phase=current_phase,
            kind=kind,
            level=level,
            source=source,
            message=message,
            slice_id=slice_id,
        )
        self.events.append(event)
        if len(self.events) > 2000:
            self.events = self.events[-1500:]
        if self.path is not None:
            try:
                HuntStore(self.path).append_event(event)
            except OSError:
                # Logging must not take down an otherwise usable workspace.
                pass
        self.event_bus.emit(event)
        return event

    def _capture(self) -> None:
        self._snapshot = self.target.fingerprint() if self.target else ""

    def attach(self, target: Target, path: Path | None, *, saved: bool) -> None:
        self.target = target
        self.path = path.resolve() if path else None
        self.gui = GuiState()
        self.chat = []
        self.hunt_note = "Idle"
        if saved:
            self._capture()
        else:
            self._snapshot = ""
        if path is not None:
            store = HuntStore(path)
            self.events = store.read_events(limit=800)
            self.settings.remember(target.name, path, target.platform.value)
            save_settings(self.settings, self.settings_path)
        else:
            self.events = []
        self.notify()

    def new_unsaved(self, target: Target) -> None:
        self.attach(target, None, saved=False)
        self.log(f"New target «{target.name}» (unsaved)", source="file")

    def open_path(self, path: Path) -> Target:
        target, target_dir = load_target(path)
        self.attach(target, target_dir, saved=True)
        self.log(f"Opened {target_dir}", source="file")
        if not target.authorized:
            self.log(
                "This target is not marked authorized. Hunt is blocked.",
                source="safety",
                level=EventLevel.WARN,
            )
        return target

    def save(self) -> Path:
        if self.target is None:
            raise ValueError("No target to save")
        if self.path is None:
            raise ValueError("No path; use Save Target As")
        save_target(self.target, self.path)
        self._capture()
        self.settings.remember(self.target.name, self.path, self.target.platform.value)
        save_settings(self.settings, self.settings_path)
        self.log(f"Saved {self.path}", source="file")
        self.notify()
        return self.path

    def save_as(self, target_dir: Path) -> Path:
        if self.target is None:
            raise ValueError("No target to save")
        store = HuntStore(target_dir)
        dest = store.save(self.target, create=not store.exists())
        self.path = dest
        self._capture()
        self.settings.remember(self.target.name, dest, self.target.platform.value)
        save_settings(self.settings, self.settings_path)
        self.log(f"Saved as {dest}", source="file")
        self.notify()
        return dest

    def close(self) -> None:
        name = self.target.name if self.target else "target"
        self.target = None
        self.path = None
        self._snapshot = ""
        self.gui = GuiState()
        self.chat = []
        self.hunt_note = "Idle"
        self.events = []
        self.log(f"Closed {name}", source="file")
        self.notify()

    def mark_dirty(self) -> None:
        self.notify()

    def status_bits(self) -> dict[str, str]:
        target = self.target
        if target is None:
            return {
                "target": "No target",
                "dirty": "",
                "hunt": "idle",
                "auth": "",
                "path": "",
                "scope": "scope —",
                "solvers": "0 solvers",
                "hunt_id": "hunt —",
            }
        return {
            "target": target.name,
            "dirty": "unsaved" if self.dirty else "saved" if self.path else "unsaved",
            "hunt": (
                f"{target.hunt_state.value} · cycle {self.gui.hunt_cycle}"
                if self.gui.hunt_cycle
                else target.hunt_state.value
            ),
            "auth": "authorized" if target.authorized else "NOT AUTHORIZED",
            "path": str(self.path) if self.path else "(memory)",
            "scope": "scope ok" if target.in_scope else "SCOPE EMPTY",
            "solvers": f"{self.gui.solvers_alive} solvers alive",
            "hunt_id": f"hunt {self.gui.hunt_id[:8]}" if self.gui.hunt_id else "hunt —",
        }

    def set_hunt_state(self, state: HuntState, note: str) -> None:
        if self.target is None:
            return
        self.target.hunt_state = state
        self.hunt_note = note
        self.notify()
