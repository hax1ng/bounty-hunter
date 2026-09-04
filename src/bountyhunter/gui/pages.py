"""NiceGUI page for the desktop-style Bounty Hunter workspace."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nicegui import app, ui
from nicegui.events import KeyEventArguments

from bountyhunter.agents.live import TargetSetupPlan, target_setup_plan
from bountyhunter.agents.stubs import assistant_chat
from bountyhunter.gui.dialogs import (
    about_dialog,
    anthropic_connection_dialog,
    confirm_if_dirty,
    new_target_dialog,
    open_target_dialog,
    openai_connection_dialog,
    save_as_dialog,
    shortcuts_dialog,
)
from bountyhunter.gui.theme import apply_theme
from bountyhunter.models import (
    PROVIDER_LABELS,
    ROLE_BLURBS,
    AgentRole,
    EventLevel,
    FindingStatus,
    HuntEvent,
    ModelProvider,
    Platform,
    ReasoningLevel,
    Target,
    default_model_for,
    model_presets_for,
    utcnow,
)
from bountyhunter.organizer.service import HuntDeps, KillSwitch, Organizer
from bountyhunter.safety import ATTESTATION, REPORTER_NO_SUBMIT, AuthorizationError
from bountyhunter.session import Session

STATIC_TABS: dict[str, str] = {
    "dashboard": "Dashboard",
    "scope": "Scope",
    "surfaces": "Surface map",
    "strategy": "Oracle",
    "findings": "Findings",
    "reports": "Report preview",
}


class BindState:
    title = "Bounty Hunter"
    path = ""
    target = "No target"
    dirty = ""
    auth = ""
    phase = "idle"
    scope = "scope —"
    solvers = "0 solvers alive"
    hunt_id = "hunt —"
    model_chip = "ChatGPT subscription first · not checked"


class Workspace:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.organizer = Organizer(session)
        self.bind = BindState()
        self.client: Any | None = None
        self.tree_host: ui.column | None = None
        self.center_host: ui.column | None = None
        self.assistant_host: ui.column | None = None
        self.roster_host: ui.column | None = None
        self.recent_host: ui.column | None = None
        self.log: ui.log | None = None
        self.tabs: ui.tabs | None = None
        self.refresh = lambda: None


def build_workspace(session: Session) -> Workspace:
    """Build the entire shell; the empty state is a view, not the application."""
    session._listeners.clear()
    session.event_bus.clear()
    apply_theme()
    ws = Workspace(session)

    with ui.header().classes("bh-header items-center no-wrap").props("dense"):
        _file_menu(ws)
        _target_menu(ws)
        _hunt_menu(ws)
        _view_menu(ws)
        _help_menu()
        ui.space()
        title_label = ui.label().bind_text_from(ws.bind, "title").classes("bh-title")
        ws.client = title_label.client

    with ui.element("div").classes("bh-toolbar row items-center no-wrap w-full"):
        _tool_button("note_add", "New", lambda: _new(ws), "Ctrl+N")
        _tool_button("folder_open", "Open", lambda: _open(ws), "Ctrl+O")
        _tool_button("save", "Save", lambda: _save(ws), "Ctrl+S")
        ui.separator().props("vertical")
        _tool_button("play_arrow", "Start", lambda: _hunt_start(ws), "Start the hunt")
        _tool_button("pause", "Pause", lambda: _hunt_pause(ws), "Pause / resume")
        ui.button("STOP", icon="stop_circle", on_click=lambda: _hunt_kill(ws)).props(
            "unelevated dense no-caps color=negative"
        ).classes("bh-kill")
        ui.separator().props("vertical")
        ui.button(
            icon="smart_toy",
            on_click=lambda: _model_setup_dialog(ws),
        ).bind_text_from(ws.bind, "model_chip").props("flat dense no-caps").classes(
            "bh-model-chip"
        ).tooltip("Choose provider, model, and reasoning")
        ui.space()
        ui.label().bind_text_from(ws.bind, "path").classes("bh-path")

    with ui.element("div").classes("bh-body"):
        with ui.splitter(value=18, limits=(12, 32)).classes("w-full h-full") as left_split:
            with left_split.before:
                with ui.column().classes("bh-pane w-full h-full no-wrap gap-0"):
                    ui.label("TARGET TREE").classes("bh-pane-head w-full")
                    ws.tree_host = ui.column().classes("w-full h-full q-pa-xs gap-0 overflow-auto")
            with left_split.after:
                with ui.splitter(horizontal=True, value=76, limits=(50, 91)).classes(
                    "w-full h-full"
                ) as bottom_split:
                    with bottom_split.before:
                        with ui.splitter(value=73, limits=(52, 88)).classes(
                            "w-full h-full"
                        ) as right_split:
                            with right_split.before:
                                ws.center_host = ui.column().classes(
                                    "bh-pane w-full h-full no-wrap gap-0"
                                )
                            with right_split.after:
                                with ui.splitter(horizontal=True, value=70, limits=(45, 86)).classes(
                                    "w-full h-full"
                                ) as agent_split:
                                    with agent_split.before:
                                        ws.assistant_host = ui.column().classes(
                                            "bh-pane w-full h-full no-wrap gap-0"
                                        )
                                    with agent_split.after:
                                        ws.roster_host = ui.column().classes(
                                            "bh-pane w-full h-full no-wrap gap-0"
                                        )
                    with bottom_split.after:
                        _bottom_drawer(ws)

    with ui.footer().classes("bh-footer row items-center no-wrap").props("dense"):
        ui.label().bind_text_from(ws.bind, "hunt_id")
        _footer_sep()
        ui.label().bind_text_from(ws.bind, "phase")
        _footer_sep()
        ui.label().bind_text_from(ws.bind, "solvers")
        _footer_sep()
        scope_label = ui.label().bind_text_from(ws.bind, "scope")
        _footer_sep()
        auth_label = ui.label().bind_text_from(ws.bind, "auth")
        _footer_sep()
        ui.label().bind_text_from(ws.bind, "dirty")
        ui.space()
        ui.label("LOCAL WORKSPACE · REPORTS REQUIRE MANUAL SUBMISSION").classes("bh-dim")

    def refresh_now() -> None:
        # Session notifications can originate from Organizer-created asyncio
        # tasks, which do not inherit NiceGUI's slot stack. Re-enter this page's
        # client explicitly before creating/rebuilding any UI elements.
        if ws.client is None or ws.client.is_deleted:
            return
        with ws.client:
            _update_bindings(ws)
            _rebuild_tree(ws)
            _rebuild_center(ws)
            _rebuild_roster(ws)
            _rebuild_recent_menu(ws)
            if session.dirty:
                title_label.classes(add="bh-title-dirty")
            else:
                title_label.classes(remove="bh-title-dirty")
            auth_label.classes(
                add="bh-ok" if session.target and session.target.authorized else "bh-bad",
                remove="bh-bad" if session.target and session.target.authorized else "bh-ok",
            )
            scope_label.classes(
                add="bh-ok" if session.target and session.target.in_scope else "bh-bad",
                remove="bh-bad" if session.target and session.target.in_scope else "bh-ok",
            )
            ui.page_title(ws.bind.title)
            ui.run_javascript(
                f"document.body.dataset.bhDirty = '{'1' if session.dirty else '0'}';"
            )

    refresh_pending = False

    def run_scheduled_refresh() -> None:
        nonlocal refresh_pending
        refresh_pending = False
        refresh_now()

    def refresh() -> None:
        """Coalesce and defer refreshes until the current UI callback returns.

        A synchronous rebuild can delete the button, dialog, or input whose
        handler is still executing. NiceGUI then sees that deleted element as
        the current slot when the handler creates a notification (or any other
        element). Running the rebuild on the next event-loop turn avoids that
        stale-slot failure, while ``refresh_now`` supplies an explicit client
        context for Organizer/background-task notifications.
        """
        nonlocal refresh_pending
        if refresh_pending or ws.client is None or ws.client.is_deleted:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            refresh_now()
            return
        refresh_pending = True
        loop.call_soon(run_scheduled_refresh)

    ws.refresh = refresh
    session.on_change(refresh)

    def on_event(event: HuntEvent) -> None:
        if ws.log is not None:
            cls = {
                EventLevel.ERROR: "text-red-4",
                EventLevel.WARN: "text-orange-4",
                EventLevel.AGENT: "text-amber-4",
                EventLevel.DEBUG: "text-grey-5",
            }.get(event.level)
            ws.log.push(event.format(), classes=cls)
        _update_bindings(ws)

    session.on_log(on_event)
    ui.keyboard(on_key=lambda e: _keys(e, ws), repeating=False)
    ui.add_head_html(
        """<script>
        window.addEventListener('beforeunload', (event) => {
          if (document.body.dataset.bhDirty === '1') {
            event.preventDefault(); event.returnValue = '';
          }
        });
        </script>"""
    )

    _rebuild_assistant(ws)
    # The first render happens while build_workspace owns a valid page slot and
    # must be complete before the workspace is returned.
    refresh_now()
    for event in session.events:
        if ws.log is not None:
            ws.log.push(event.format())
    session.log("Workspace ready", source="workspace")
    return ws


def build_page(session: Session | None = None) -> Workspace:
    return build_workspace(session or Session())


def _tool_button(icon: str, label: str, callback, tip: str) -> None:
    ui.button(label, icon=icon, on_click=callback).props("flat dense no-caps").tooltip(tip)


def _footer_sep() -> None:
    ui.separator().props("vertical")


def _file_menu(ws: Workspace) -> None:
    with ui.button("File").props("flat dense no-caps").classes("bh-menu-btn"):
        with ui.menu().props("dense"):
            ui.menu_item("New Target…        Ctrl+N", on_click=lambda: _new(ws))
            ui.menu_item("Open Target…       Ctrl+O", on_click=lambda: _open(ws))
            with ui.menu_item("Recent Targets", auto_close=False):
                with ui.item_section().props("side"):
                    ui.icon("keyboard_arrow_right")
                with ui.menu().props('anchor="top end" self="top start" auto-close dense'):
                    ws.recent_host = ui.column().classes("gap-0")
                    _rebuild_recent_menu(ws)
            ui.separator()
            ui.menu_item("Save                 Ctrl+S", on_click=lambda: _save(ws))
            ui.menu_item("Save Target As…", on_click=lambda: _save_as(ws))
            ui.separator()
            ui.menu_item("Close Target", on_click=lambda: _close(ws))
            ui.menu_item("Quit", on_click=lambda: _quit(ws))


def _target_menu(ws: Workspace) -> None:
    with ui.button("Target").props("flat dense no-caps").classes("bh-menu-btn"):
        with ui.menu().props("auto-close dense"):
            ui.menu_item("Set up from any information…", on_click=lambda: _smart_setup_dialog(ws))
            ui.menu_item("Choose AI models…", on_click=lambda: _model_setup_dialog(ws))
            ui.separator()
            ui.menu_item("OpenAI connection…", on_click=lambda: _openai_dialog(ws))
            ui.menu_item("Anthropic connection…", on_click=lambda: _anthropic_dialog(ws))
            ui.separator()
            ui.menu_item("Dashboard", on_click=lambda: _select_tab(ws, "dashboard"))
            ui.menu_item("Scope & model policy", on_click=lambda: _select_tab(ws, "scope"))
            ui.menu_item("Surface map", on_click=lambda: _select_tab(ws, "surfaces"))
            ui.menu_item("Oracle directives", on_click=lambda: _select_tab(ws, "strategy"))
            ui.menu_item("Findings", on_click=lambda: _select_tab(ws, "findings"))
            ui.menu_item("Report preview", on_click=lambda: _select_tab(ws, "reports"))


def _hunt_menu(ws: Workspace) -> None:
    with ui.button("Hunt").props("flat dense no-caps").classes("bh-menu-btn"):
        with ui.menu().props("auto-close dense"):
            ui.menu_item("Start", on_click=lambda: _hunt_start(ws))
            ui.menu_item("Pause / Resume", on_click=lambda: _hunt_pause(ws))
            ui.separator()
            ui.menu_item("Stop", on_click=lambda: _hunt_kill(ws)).classes("text-red-4")


def _view_menu(ws: Workspace) -> None:
    with ui.button("View").props("flat dense no-caps").classes("bh-menu-btn"):
        with ui.menu().props("auto-close dense"):
            ui.menu_item("Dashboard", on_click=lambda: _select_tab(ws, "dashboard"))
            ui.menu_item("Event log", on_click=lambda: setattr(ws.session.gui, "event_drawer_open", True))


def _help_menu() -> None:
    with ui.button("Help").props("flat dense no-caps").classes("bh-menu-btn"):
        with ui.menu().props("auto-close dense"):
            ui.menu_item("Keyboard shortcuts", on_click=shortcuts_dialog)
            ui.menu_item(
                "Safety",
                on_click=lambda: ui.notify(
                    "Authorized targets only. Reporter never auto-submits.", type="info"
                ),
            )
            ui.menu_item("About", on_click=about_dialog)


def _bottom_drawer(ws: Workspace) -> None:
    with ui.column().classes("w-full h-full no-wrap gap-0"):
        with ui.tabs(value="events").classes("bh-log-tabs w-full").props("dense") as tabs:
            ui.tab("events", label="Event log")
            ui.tab("console", label="Organizer console")
        with ui.tab_panels(tabs, value="events").classes("w-full flex-1 bh-log-panels"):
            with ui.tab_panel("events").classes("q-pa-none"):
                ws.log = ui.log(max_lines=1200).classes("bh-log w-full h-full")
            with ui.tab_panel("console").classes("q-pa-sm bh-console"):
                ui.label("Organizer owns the asyncio graph. ScopeGuard runs before every queued task.")
                ui.label("Assistant does not await or invoke Mapping/Solver.").classes("bh-dim")


def _update_bindings(ws: Workspace) -> None:
    bits = ws.session.status_bits()
    ws.bind.title = ws.session.window_title
    ws.bind.path = bits["path"]
    ws.bind.target = bits["target"]
    ws.bind.dirty = bits["dirty"]
    ws.bind.auth = bits["auth"]
    ws.bind.phase = bits["hunt"]
    ws.bind.scope = bits["scope"]
    ws.bind.solvers = bits["solvers"]
    ws.bind.hunt_id = bits["hunt_id"]
    target = ws.session.target
    if target is None:
        ws.bind.model_chip = ws.session.openai.status
    else:
        cfg = target.agent_config[AgentRole.ASSISTANT.value]
        connection = ws.session.connection_for(cfg.provider)
        if cfg.provider is ModelProvider.LOCAL or not cfg.use_llm:
            mode = "stub"
        elif connection.use_subscription_first:
            mode = "subscription→API"
        else:
            mode = "API"
        provider_label = PROVIDER_LABELS.get(cfg.provider, cfg.provider.value)
        ws.bind.model_chip = (
            f"Assistant · {provider_label} · {cfg.model} · {cfg.reasoning.value} · {mode}"
        )


def _openai_dialog(ws: Workspace) -> None:
    openai_connection_dialog(ws.session, on_done=ws.refresh)


def _anthropic_dialog(ws: Workspace) -> None:
    anthropic_connection_dialog(ws.session, on_done=ws.refresh)


def _connection_dialog_for(ws: Workspace, provider: ModelProvider) -> None:
    if provider is ModelProvider.ANTHROPIC:
        _anthropic_dialog(ws)
    else:
        _openai_dialog(ws)


def _model_setup_dialog(
    ws: Workspace, initial_role: AgentRole = AgentRole.ASSISTANT
) -> None:
    """Prominent, searchable model policy editor used from the dashboard/toolbar."""
    target = ws.session.target
    if target is None:
        _smart_setup_dialog(ws)
        return

    hosted = [ModelProvider.OPENAI, ModelProvider.ANTHROPIC]
    initial = target.agent_config[initial_role.value]
    initial_provider = initial.provider if initial.provider in hosted else ModelProvider.OPENAI
    dialog = ui.dialog()
    with dialog, ui.card().classes("w-[620px] max-w-[94vw]"):
        ui.label("Choose AI models").classes("text-base font-medium")
        ui.label(
            "Pick a role, then search or type a model. Changes are stored with this Target."
        ).classes("bh-dim")
        role = ui.select(
            {item.value: item.value.title() for item in AgentRole},
            value=initial_role.value,
            label="Agent role",
        ).props("outlined dense options-dense").classes("w-full")
        provider = ui.select(
            {item.value: PROVIDER_LABELS[item] for item in hosted},
            value=initial_provider.value,
            label="Provider",
        ).props("outlined dense options-dense").classes("w-full")
        model = ui.select(
            list(dict.fromkeys([*model_presets_for(initial_provider), initial.model])),
            value=initial.model,
            label="Search or enter model",
            with_input=True,
            new_value_mode="add-unique",
        ).props("outlined dense options-dense use-input input-debounce=0").classes("w-full")
        reasoning = ui.select(
            {item.value: item.value for item in ReasoningLevel},
            value=initial.reasoning.value,
            label="Reasoning effort",
        ).props("outlined dense options-dense").classes("w-full")
        with ui.row().classes("w-full items-center"):
            enabled = ui.switch("Role enabled", value=initial.enabled)
            live = ui.switch("Use hosted AI", value=initial.use_llm)
        status = ui.label().classes("bh-dim")

        def current_provider() -> ModelProvider:
            return ModelProvider(provider.value)

        def refresh_status() -> None:
            connection = ws.session.connection_for(current_provider())
            status.set_text(connection.status)
            status.classes(
                add="bh-ok" if connection.ready else "bh-bad",
                remove="bh-bad bh-dim" if connection.ready else "bh-ok bh-dim",
            )

        def set_provider(*, preserve: bool) -> None:
            selected_provider = current_provider()
            desired = str(model.value or "") if preserve else ""
            desired = desired or default_model_for(selected_provider)
            options = list(dict.fromkeys([*model_presets_for(selected_provider), desired]))
            model.set_options(options, value=desired)
            refresh_status()

        def load_role() -> None:
            assert ws.session.target is not None
            selected = ws.session.target.agent_config[role.value]
            chosen_provider = (
                selected.provider if selected.provider in hosted else ModelProvider.OPENAI
            )
            provider.value = chosen_provider.value
            model.set_options(
                list(dict.fromkeys([*model_presets_for(chosen_provider), selected.model])),
                value=selected.model or default_model_for(chosen_provider),
            )
            reasoning.value = selected.reasoning.value
            enabled.value = selected.enabled
            live.value = selected.use_llm
            refresh_status()

        def update_config(config) -> None:
            chosen_provider = current_provider()
            fallback = default_model_for(chosen_provider)
            config.provider = chosen_provider
            config.model = str(model.value or fallback).strip() or fallback
            config.reasoning = ReasoningLevel(reasoning.value)
            config.enabled = bool(enabled.value)
            config.use_llm = bool(live.value)

        def apply_one() -> None:
            if ws.organizer.running:
                ui.notify("Stop the running hunt before changing models", type="warning")
                return
            assert ws.session.target is not None
            update_config(ws.session.target.agent_config[role.value])
            ws.session.mark_dirty()
            ui.notify(f"Updated {role.value.title()} model", type="positive")

        def apply_all() -> None:
            if ws.organizer.running:
                ui.notify("Stop the running hunt before changing models", type="warning")
                return
            assert ws.session.target is not None
            for config in ws.session.target.agent_config.values():
                was_enabled = config.enabled
                update_config(config)
                config.enabled = was_enabled
            ws.session.mark_dirty()
            ui.notify(
                "Applied this provider and model to every role; enabled roles were unchanged",
                type="positive",
            )

        provider.on_value_change(lambda _: set_provider(preserve=False))
        role.on_value_change(lambda _: load_role())
        refresh_status()
        with ui.row().classes("w-full items-center"):
            ui.button(
                "Connection settings",
                icon="key",
                on_click=lambda: _connection_dialog_for(ws, current_provider()),
            ).props("flat dense no-caps")
            ui.space()
            ui.button("Close", on_click=dialog.close).props("flat dense no-caps")
            ui.button("Apply to all roles", on_click=apply_all).props("flat dense no-caps")
            ui.button("Apply role", on_click=apply_one).props("unelevated dense no-caps")
    dialog.open()


def _smart_setup_dialog(ws: Workspace) -> None:
    """Use a selected hosted model to extract a complete Target draft from pasted text."""
    current = ws.session.target
    if ws.organizer.running:
        ui.notify("Stop the running hunt before changing Target setup", type="warning")
        return
    hosted = [ModelProvider.OPENAI, ModelProvider.ANTHROPIC]
    seed = (
        current.agent_config[AgentRole.ASSISTANT.value]
        if current is not None
        else Target(name="Setup draft").agent_config[AgentRole.ASSISTANT.value]
    )
    initial_provider = seed.provider if seed.provider in hosted else ModelProvider.OPENAI
    dialog = ui.dialog().props("persistent")
    plan_state: dict[str, TargetSetupPlan | None] = {"plan": None}
    with dialog, ui.card().classes(
        "w-[760px] max-w-[95vw] max-h-[92vh] overflow-auto"
    ):
        ui.label("Set up Target with AI").classes("text-base font-medium")
        ui.label(
            "Paste a program page, scope table, notes, URLs, or a rough description. "
            "The model only organizes what you provide; it does not browse or test anything."
        ).classes("bh-dim")
        material = ui.textarea(
            "Program information",
            placeholder=(
                "Paste all available information here. Include explicit in-scope and "
                "out-of-scope assets when possible."
            ),
        ).props("outlined rows=10").classes("w-full bh-mono-input")
        with ui.row().classes("w-full no-wrap gap-2"):
            provider = ui.select(
                {item.value: PROVIDER_LABELS[item] for item in hosted},
                value=initial_provider.value,
                label="Setup provider",
            ).props("outlined dense options-dense").classes("w-1/3")
            model = ui.select(
                list(dict.fromkeys([*model_presets_for(initial_provider), seed.model])),
                value=seed.model,
                label="Search or enter setup model",
                with_input=True,
                new_value_mode="add-unique",
            ).props("outlined dense options-dense use-input input-debounce=0").classes("w-1/3")
            reasoning = ui.select(
                {item.value: item.value for item in ReasoningLevel},
                value=seed.reasoning.value,
                label="Reasoning",
            ).props("outlined dense options-dense").classes("w-1/3")
        connection_status = ui.label().classes("bh-dim")
        authorization = ui.checkbox(
            ATTESTATION,
            value=bool(current and current.authorized),
        ).classes("text-xs")
        configure_hunt = ui.checkbox(
            "Use this provider and model for every enabled hunt role",
            value=True,
        ).classes("text-xs")
        ui.label(
            "Recommended for guided setup. Hosted usage begins only when you press Start."
        ).classes("bh-dim text-xs")
        ui.label(
            "The model cannot grant authorization. You must confirm this yourself before applying."
        ).classes("bh-bad text-xs")
        ui.separator()
        ui.label("REVIEW DRAFT").classes("bh-subhead")
        preview = ui.column().classes("w-full gap-1 bh-setup-preview")
        with preview:
            ui.label("Analyze your information to create a reviewable draft.").classes("bh-dim")

        def selected_provider() -> ModelProvider:
            return ModelProvider(provider.value)

        def refresh_connection() -> None:
            connection = ws.session.connection_for(selected_provider())
            connection_status.set_text(connection.status)
            connection_status.classes(
                add="bh-ok" if connection.ready else "bh-bad",
                remove="bh-bad bh-dim" if connection.ready else "bh-ok bh-dim",
            )

        def sync_provider() -> None:
            chosen = selected_provider()
            selected_model = default_model_for(chosen)
            model.set_options(model_presets_for(chosen), value=selected_model)
            refresh_connection()

        def render_plan(plan: TargetSetupPlan) -> None:
            preview.clear()
            with preview:
                _kv("Name", plan.name)
                _kv("Platform", plan.platform.value)
                _kv("Program URL", plan.program_url or "—", mono=True)
                ui.label(f"IN SCOPE ({len(plan.in_scope)})").classes("bh-subhead")
                for asset in plan.in_scope:
                    ui.label(asset).classes("bh-mono")
                if not plan.in_scope:
                    ui.label("No explicit in-scope assets found — add them before starting.").classes(
                        "bh-bad"
                    )
                ui.label(f"OUT OF SCOPE ({len(plan.out_of_scope)})").classes("bh-subhead")
                for asset in plan.out_of_scope:
                    ui.label(asset).classes("bh-mono bh-dim")
                ui.label("NOTES").classes("bh-subhead")
                ui.label(plan.notes or "No notes extracted.").classes("whitespace-pre-wrap")

        analyze_button = None
        apply_button = None

        async def analyze() -> None:
            raw = (material.value or "").strip()
            if not raw:
                ui.notify("Paste some program information first", type="warning")
                return
            if len(raw) > 100_000:
                ui.notify("Program information is limited to 100,000 characters", type="warning")
                return
            assert analyze_button is not None and apply_button is not None
            analyze_button.set_enabled(False)
            apply_button.set_enabled(False)
            connection_status.set_text("Analyzing…")
            try:
                draft_target = (
                    ws.session.target.model_copy(deep=True)
                    if ws.session.target is not None
                    else Target(name="Setup draft")
                )
                config = draft_target.agent_config[AgentRole.ASSISTANT.value]
                chosen = selected_provider()
                config.provider = chosen
                config.model = str(model.value or default_model_for(chosen)).strip()
                config.reasoning = ReasoningLevel(reasoning.value)
                config.enabled = True
                config.use_llm = True
                deps = HuntDeps(
                    session=ws.session,
                    target=draft_target,
                    event_bus=ws.session.event_bus,
                    kill_switch=KillSwitch(),
                    hunt_id=ws.session.gui.hunt_id or "target-setup",
                )
                plan = await target_setup_plan(deps, raw)
                plan_state["plan"] = plan
                render_plan(plan)
                apply_button.set_enabled(True)
                ui.notify("Setup draft is ready for review", type="positive")
            except Exception as exc:
                plan_state["plan"] = None
                detail = str(exc).strip()[:700] or type(exc).__name__
                connection_status.set_text(f"Setup failed: {detail}")
                ui.notify(detail, type="negative", timeout=6000)
            finally:
                analyze_button.set_enabled(True)
                refresh_connection()

        def apply_plan() -> None:
            plan = plan_state["plan"]
            if plan is None:
                ui.notify("Analyze the information first", type="warning")
                return
            if not authorization.value:
                ui.notify("Authorization confirmation is required", type="negative")
                return
            if ws.organizer.running:
                ui.notify("Stop the running hunt before applying setup", type="warning")
                return

            existing = ws.session.target
            chosen = selected_provider()
            selected_model = str(model.value or default_model_for(chosen)).strip()
            if existing is None:
                updated = Target(
                    name=plan.name,
                    platform=plan.platform,
                    program_url=plan.program_url,
                    in_scope=plan.in_scope,
                    out_of_scope=plan.out_of_scope,
                    notes=plan.notes,
                    authorized=True,
                    authorization_attestation=ATTESTATION,
                    authorized_at=utcnow(),
                    continuous_hunt=True,
                )
            else:
                updated = existing
                scope_changed = (
                    updated.in_scope != plan.in_scope
                    or updated.out_of_scope != plan.out_of_scope
                )
                updated.name = plan.name
                updated.platform = plan.platform
                updated.program_url = plan.program_url
                updated.in_scope = plan.in_scope
                updated.out_of_scope = plan.out_of_scope
                updated.notes = plan.notes
                updated.authorized = True
                updated.authorization_attestation = ATTESTATION
                updated.authorized_at = updated.authorized_at or utcnow()
                if scope_changed:
                    updated.slices = []
                    updated.findings = []
                    updated.hypotheses = []
                    updated.chains = []
                    updated.escalations = []
                    updated.reports = []
            config = updated.agent_config[AgentRole.ASSISTANT.value]
            config.provider = chosen
            config.model = selected_model
            config.reasoning = ReasoningLevel(reasoning.value)
            config.enabled = True
            config.use_llm = True
            if configure_hunt.value:
                for role_config in updated.agent_config.values():
                    role_config.provider = chosen
                    role_config.model = selected_model
                    role_config.reasoning = ReasoningLevel(reasoning.value)
                    role_config.use_llm = True
            dialog.close()
            if existing is None:
                ws.session.new_unsaved(updated)
                _after_target_change(ws)
            else:
                ws.session.mark_dirty()
            ui.notify("Target setup applied — review Scope, then Save", type="positive")

        provider.on_value_change(lambda _: sync_provider())
        refresh_connection()
        with ui.row().classes("w-full items-center"):
            ui.button(
                "Connection settings",
                icon="key",
                on_click=lambda: _connection_dialog_for(ws, selected_provider()),
            ).props("flat dense no-caps")
            ui.space()
            ui.button("Cancel", on_click=dialog.close).props("flat dense no-caps")
            analyze_button = ui.button(
                "Analyze information", icon="auto_awesome", on_click=analyze
            ).props("flat dense no-caps")
            apply_button = ui.button(
                "Apply setup", icon="check", on_click=apply_plan
            ).props("unelevated dense no-caps")
            apply_button.set_enabled(False)
    dialog.open()


def _keys(event: KeyEventArguments, ws: Workspace) -> None:
    if not event.action.keydown or event.action.repeat:
        return
    key = str(event.key).lower()
    if event.modifiers.ctrl and event.modifiers.shift and key == "s":
        _save_as(ws)
    elif event.modifiers.ctrl and key == "n":
        _new(ws)
    elif event.modifiers.ctrl and key == "o":
        _open(ws)
    elif event.modifiers.ctrl and key == "s":
        _save(ws)


def _new(ws: Workspace) -> None:
    def go() -> None:
        if ws.organizer.running:
            ws.organizer.kill()
        new_target_dialog(ws.session, on_done=lambda: _after_target_change(ws))

    confirm_if_dirty(ws.session, then=go)


def _open(ws: Workspace) -> None:
    def go() -> None:
        if ws.organizer.running:
            ws.organizer.kill()
        open_target_dialog(ws.session, on_done=lambda: _after_target_change(ws))

    confirm_if_dirty(ws.session, then=go)


def _open_recent(ws: Workspace, path: str) -> None:
    def go() -> None:
        try:
            if ws.organizer.running:
                ws.organizer.kill()
            ws.session.open_path(Path(path))
            _after_target_change(ws)
        except Exception as exc:
            ui.notify(str(exc), type="negative")

    confirm_if_dirty(ws.session, then=go)


def _rebuild_recent_menu(ws: Workspace) -> None:
    if ws.recent_host is None:
        return
    ws.recent_host.clear()
    with ws.recent_host:
        if not ws.session.settings.recent:
            ui.menu_item("(empty)").props("disable")
            return
        for item in ws.session.settings.recent:
            ui.menu_item(
                item.name,
                on_click=lambda p=item.path: _open_recent(ws, p),
            ).tooltip(item.path)
        ui.separator()
        ui.menu_item("Clear recent", on_click=lambda: _clear_recent(ws))


def _clear_recent(ws: Workspace) -> None:
    from bountyhunter.settings import save_settings

    ws.session.settings.clear_recent()
    save_settings(ws.session.settings, ws.session.settings_path)
    _rebuild_recent_menu(ws)


def _after_target_change(ws: Workspace) -> None:
    ws.organizer = Organizer(ws.session)
    _rebuild_assistant(ws)
    ws.refresh()


def _save(ws: Workspace) -> None:
    if ws.session.target is None:
        ui.notify("No Target is open", type="warning")
        return
    if ws.session.path is None:
        _save_as(ws)
        return
    try:
        ws.session.save()
        ui.notify("Target saved", type="positive")
    except Exception as exc:
        ui.notify(str(exc), type="negative")


def _save_as(ws: Workspace) -> None:
    save_as_dialog(ws.session, on_done=ws.refresh)


def _close(ws: Workspace) -> None:
    def go() -> None:
        if ws.session.target is not None:
            if ws.organizer.running:
                ws.organizer.kill()
            ws.session.close()
            _after_target_change(ws)

    confirm_if_dirty(ws.session, then=go)


def _quit(ws: Workspace) -> None:
    def go() -> None:
        if ws.organizer.running:
            ws.organizer.kill()
        app.shutdown()

    confirm_if_dirty(ws.session, then=go)


def _hunt_start(ws: Workspace) -> None:
    target = ws.session.target
    if target is not None and target.continuous_hunt:
        hunt_roles = set(AgentRole) - {AgentRole.ASSISTANT}
        if not any(
            config.is_live
            for config in target.agent_config.values()
            if config.role in hunt_roles
        ):
            ui.notify(
                "Continuous mode has no hosted hunt model enabled. Turn on Use hosted AI "
                "and apply the model to all roles; otherwise only demo stub results repeat.",
                type="warning",
                timeout=7000,
            )
            _model_setup_dialog(ws, AgentRole.SOLVER)
            return
    try:
        ws.organizer.start()
        ws.refresh()
    except (AuthorizationError, ValueError) as exc:
        ws.session.log(str(exc), source="safety", level=EventLevel.WARN)
        ui.notify(str(exc), type="negative")


def _hunt_pause(ws: Workspace) -> None:
    note = ws.organizer.pause()
    ws.refresh()
    ui.notify(note, type="info")


def _hunt_kill(ws: Workspace) -> None:
    note = ws.organizer.kill()
    ws.refresh()
    ui.notify(note, type="warning")


def _tree_nodes(session: Session) -> list[dict[str, Any]]:
    target = session.target
    if target is None:
        return [{"id": "none", "label": "(no target open)"}]
    in_assets = [{"id": f"asset-in:{i}", "label": asset} for i, asset in enumerate(target.in_scope)]
    out_assets = [{"id": f"asset-out:{i}", "label": asset} for i, asset in enumerate(target.out_of_scope)]
    return [{
        "id": "target",
        "label": target.name,
        "children": [
            {"id": "scope", "label": "Scope", "children": [
                {"id": "scope-in", "label": f"In scope ({len(in_assets)})", "children": in_assets},
                {"id": "scope-out", "label": f"Out of scope ({len(out_assets)})", "children": out_assets},
            ]},
            {"id": "surfaces", "label": f"Surfaces ({len(target.slices)})", "children": [
                {"id": f"slice:{surface.id}", "label": surface.title} for surface in target.slices
            ]},
            {
                "id": "strategy",
                "label": (
                    "Oracle directives "
                    f"({len(target.hypotheses) + len(target.chains) + len(target.escalations)})"
                ),
            },
            {"id": "findings", "label": f"Findings ({len(target.findings)})", "children": [
                {"id": f"finding:{finding.id}", "label": finding.title} for finding in target.findings
            ]},
            {"id": "reports", "label": f"Reports ({len(target.reports)})", "children": [
                {"id": f"report:{report.id}", "label": report.title} for report in target.reports
            ]},
        ],
    }]


def _rebuild_tree(ws: Workspace) -> None:
    if ws.tree_host is None:
        return
    ws.tree_host.clear()
    with ws.tree_host:
        tree = ui.tree(
            _tree_nodes(ws.session), node_key="id", label_key="label",
            on_select=lambda event: _tree_select(ws, event.value),
        ).props("dense no-connectors").classes("w-full bh-tree")
        tree.expand()


def _tree_select(ws: Workspace, node_id: str | None) -> None:
    if node_id is None:
        return
    if node_id.startswith("slice:"):
        _open_detail_tab(ws, "slice", node_id.split(":", 1)[1])
    elif node_id.startswith("finding:"):
        _open_detail_tab(ws, "finding", node_id.split(":", 1)[1])
    elif node_id.startswith("report:"):
        _open_detail_tab(ws, "report", node_id.split(":", 1)[1])
    elif node_id in {"scope", "scope-in", "scope-out"} or node_id.startswith("asset-"):
        _select_tab(ws, "scope")
    elif node_id in STATIC_TABS:
        _select_tab(ws, node_id)
    elif node_id == "target":
        _select_tab(ws, "dashboard")


def _detail_label(ws: Workspace, key: str) -> str:
    kind, item_id = key.split(":", 1)
    target = ws.session.target
    if target is None:
        return item_id
    if kind == "slice":
        item = next((x for x in target.slices if x.id == item_id), None)
    elif kind == "finding":
        item = next((x for x in target.findings if x.id == item_id), None)
    else:
        item = next((x for x in target.reports if x.id == item_id), None)
    return getattr(item, "title", item_id)


def _open_detail_tab(ws: Workspace, kind: str, item_id: str) -> None:
    key = f"{kind}:{item_id}"
    if key not in ws.session.gui.open_tabs:
        ws.session.gui.open_tabs.append(key)
    ws.session.gui.active_tab = key
    _rebuild_center(ws)


def _close_detail_tab(ws: Workspace, key: str) -> None:
    if key in ws.session.gui.open_tabs:
        ws.session.gui.open_tabs.remove(key)
    if ws.session.gui.active_tab == key:
        ws.session.gui.active_tab = "dashboard"
    _rebuild_center(ws)


def _select_tab(ws: Workspace, key: str) -> None:
    ws.session.gui.active_tab = key
    if ws.tabs is not None:
        ws.tabs.set_value(key)


def _rebuild_center(ws: Workspace) -> None:
    if ws.center_host is None:
        return
    ws.center_host.clear()
    with ws.center_host:
        if ws.session.target is None:
            _empty_state(ws)
            ws.tabs = None
            return
        valid = set(STATIC_TABS) | set(ws.session.gui.open_tabs)
        if ws.session.gui.active_tab not in valid:
            ws.session.gui.active_tab = "dashboard"
        with ui.tabs(value=ws.session.gui.active_tab).classes("bh-main-tabs w-full").props(
            "dense inline-label align=left"
        ) as tabs:
            for key, label in STATIC_TABS.items():
                ui.tab(key, label=label)
            for key in ws.session.gui.open_tabs:
                with ui.tab(key, label=""):
                    ui.label(_detail_label(ws, key)).classes("truncate max-w-40")
                    ui.button(
                        icon="close",
                        on_click=lambda _=None, tab_key=key: _close_detail_tab(ws, tab_key),
                    ).props("flat round dense size=xs").classes("bh-tab-close")
        tabs.on_value_change(lambda event: setattr(ws.session.gui, "active_tab", event.value))
        ws.tabs = tabs
        with ui.tab_panels(tabs, value=ws.session.gui.active_tab).classes(
            "w-full flex-1 bh-main-panels overflow-auto"
        ):
            with ui.tab_panel("dashboard"):
                _dashboard(ws)
            with ui.tab_panel("scope"):
                _scope_view(ws)
            with ui.tab_panel("surfaces"):
                _surface_map(ws)
            with ui.tab_panel("strategy"):
                _strategy_view(ws)
            with ui.tab_panel("findings"):
                _findings_view(ws)
            with ui.tab_panel("reports"):
                _reports_view(ws)
            for key in ws.session.gui.open_tabs:
                with ui.tab_panel(key):
                    kind, item_id = key.split(":", 1)
                    if kind == "slice":
                        _slice_inspector(ws, item_id)
                    elif kind == "finding":
                        _finding_detail(ws, item_id)
                    else:
                        _report_detail(ws, item_id)


def _empty_state(ws: Workspace) -> None:
    with ui.column().classes("w-full h-full items-center justify-center gap-2"):
        ui.label("NO TARGET OPEN").classes("bh-section-title")
        ui.label("Paste what you have for guided setup, or create/open a Target manually.").classes(
            "bh-dim"
        )
        with ui.row().classes("q-mt-sm"):
            ui.button(
                "Set up with AI",
                icon="auto_awesome",
                on_click=lambda: _smart_setup_dialog(ws),
            ).props("unelevated dense no-caps")
            ui.button("New Target…", icon="note_add", on_click=lambda: _new(ws)).props("unelevated dense no-caps")
            ui.button("Open Target…", icon="folder_open", on_click=lambda: _open(ws)).props("flat dense no-caps")


def _section(title: str, subtitle: str = "") -> None:
    ui.label(title.upper()).classes("bh-section-title")
    if subtitle:
        ui.label(subtitle).classes("bh-dim q-mb-xs")


def _dashboard(ws: Workspace) -> None:
    target = ws.session.target
    assert target is not None
    _section("Dashboard", f"{target.name} · {target.platform.value}")
    with ui.row().classes("w-full items-center q-my-xs bh-dashboard-actions"):
        ui.button(
            "Set up from information",
            icon="auto_awesome",
            on_click=lambda: _smart_setup_dialog(ws),
        ).props("unelevated dense no-caps")
        ui.button(
            "Choose AI models",
            icon="smart_toy",
            on_click=lambda: _model_setup_dialog(ws),
        ).props("outline dense no-caps")
        ui.space()
        mode = "CONTINUOUS" if target.continuous_hunt else "ONE PASS"
        limit = (
            f" · {target.hunt_time_limit_minutes} min timer"
            if target.hunt_time_limit_minutes
            else " · stop manually"
        )
        ui.label(mode + limit).classes("bh-ok" if target.continuous_hunt else "bh-dim")
    reportable = sum(f.status is FindingStatus.REPORTABLE for f in target.findings)
    with ui.row().classes("w-full gap-0 bh-stat-strip"):
        _stat("PHASE", target.hunt_state.value)
        _stat("SLICES", str(len(target.slices)))
        _stat("SOLVERS", str(ws.session.gui.solvers_alive))
        _stat(
            "ORACLE",
            str(len(target.hypotheses) + len(target.chains) + len(target.escalations)),
        )
        _stat("PAST GATES", str(reportable))
        _stat("REPORTS", str(len(target.reports)))
    ui.separator()
    with ui.row().classes("w-full no-wrap gap-6"):
        with ui.column().classes("w-1/2 gap-1"):
            ui.label("TARGET CONTROL").classes("bh-subhead")
            _kv("Authorization", "authorized" if target.authorized else "NOT AUTHORIZED")
            _kv("Scope assets", str(len(target.in_scope)))
            _kv("Project path", str(ws.session.path or "unsaved"), mono=True)
            _kv("Hunt id", ws.session.gui.hunt_id or "—", mono=True)
            ui.label("RUN UNTIL STOPPED").classes("bh-subhead q-mt-sm")
            continuous = ui.switch(
                "Keep starting new hunt cycles until Stop",
                value=target.continuous_hunt,
            )
            with ui.row().classes("w-full no-wrap gap-2"):
                timer = ui.number(
                    "Timer in minutes (0 = no timer)",
                    value=target.hunt_time_limit_minutes,
                    min=0,
                    max=10_080,
                    step=1,
                ).props("outlined dense").classes("w-1/2")
                delay = ui.number(
                    "Seconds between cycles",
                    value=target.hunt_cycle_delay_seconds,
                    min=1,
                    max=3_600,
                    step=1,
                ).props("outlined dense").classes("w-1/2")

            def apply_run_mode() -> None:
                if ws.organizer.running:
                    ui.notify("Stop the running hunt before changing its timer", type="warning")
                    return
                assert ws.session.target is not None
                ws.session.target.continuous_hunt = bool(continuous.value)
                ws.session.target.hunt_time_limit_minutes = max(0, int(timer.value or 0))
                ws.session.target.hunt_cycle_delay_seconds = max(1, int(delay.value or 30))
                ws.session.mark_dirty()
                ui.notify("Hunt run mode updated", type="positive")

            ui.button("Apply run mode", on_click=apply_run_mode).props(
                "flat dense no-caps"
            )
        with ui.column().classes("w-1/2 gap-1"):
            ui.label("LAST EVENTS").classes("bh-subhead")
            if not ws.session.events:
                ui.label("No events yet.").classes("bh-dim")
            for event in ws.session.events[-8:]:
                ui.label(event.format()).classes("bh-event-line")


def _stat(label: str, value: str) -> None:
    with ui.column().classes("bh-stat gap-0"):
        ui.label(value).classes("bh-stat-value")
        ui.label(label).classes("bh-stat-label")


def _kv(key: str, value: str, *, mono: bool = False) -> None:
    with ui.row().classes("w-full no-wrap items-baseline"):
        ui.label(key).classes("bh-kv-key")
        ui.label(value).classes("bh-mono" if mono else "")


def _scope_view(ws: Workspace) -> None:
    target = ws.session.target
    assert target is not None
    _section("Scope & model policy", "ScopeGuard checks this data before every queued task.")
    with ui.row().classes("w-full no-wrap gap-3"):
        with ui.column().classes("w-2/3 gap-2"):
            platform = ui.select(
                {p.value: p.value.title() for p in Platform}, value=target.platform.value, label="Platform"
            ).props("outlined dense options-dense").classes("w-full")
            program = ui.input("Program URL", value=target.program_url).props("outlined dense").classes("w-full bh-mono-input")
            in_scope = ui.textarea("In scope — one asset per line", value=target.in_scope_text()).props("outlined dense rows=6").classes("w-full bh-mono-input")
            out_scope = ui.textarea("Out of scope — one asset per line", value=target.out_of_scope_text()).props("outlined dense rows=5").classes("w-full bh-mono-input")
            notes = ui.textarea(
                "Operator notes — verified observations, reproduction steps, and artifact paths",
                value=target.notes,
            ).props("outlined dense rows=7").classes("w-full bh-mono-input")
            authorized = ui.checkbox(ATTESTATION, value=target.authorized).classes("text-xs")

            def apply_scope() -> None:
                assert ws.session.target is not None
                if ws.organizer.running:
                    ui.notify(
                        "Pause is not enough to edit scope; kill or finish the running work first.",
                        type="warning",
                    )
                    return
                ws.session.target.platform = Platform(platform.value)
                ws.session.target.program_url = (program.value or "").strip()
                ws.session.target.set_scope_from_text(in_scope.value or "", out_scope.value or "")
                ws.session.target.notes = notes.value or ""
                ws.session.target.authorized = bool(authorized.value)
                if authorized.value:
                    ws.session.target.authorization_attestation = ATTESTATION
                    ws.session.target.authorized_at = ws.session.target.authorized_at or utcnow()
                ws.session.mark_dirty()
                ui.notify("Scope updated; File → Save to persist", type="info")

            ui.button("Apply scope", on_click=apply_scope).props("unelevated dense no-caps")
        with ui.column().classes("w-1/3 gap-2"):
            ui.label("MODEL POLICY PER ROLE").classes("bh-subhead")
            role = ui.select({r.value: r.value.title() for r in AgentRole}, value=AgentRole.ASSISTANT.value, label="Role").props("outlined dense options-dense").classes("w-full")
            cfg = target.agent_config[role.value]
            hosted_providers = [ModelProvider.OPENAI, ModelProvider.ANTHROPIC]
            initial_provider = (
                cfg.provider if cfg.provider in hosted_providers else ModelProvider.OPENAI
            )
            provider = ui.select(
                {p.value: PROVIDER_LABELS[p] for p in hosted_providers},
                value=initial_provider.value,
                label="Provider",
            ).props("outlined dense options-dense").classes("w-full")
            initial_models = list(
                dict.fromkeys([*model_presets_for(initial_provider), cfg.model])
            )
            model = ui.select(
                initial_models,
                value=cfg.model,
                label="Model",
                with_input=True,
                new_value_mode="add-unique",
            ).props("outlined dense options-dense").classes("w-full")
            reasoning = ui.select({r.value: r.value for r in ReasoningLevel}, value=cfg.reasoning.value, label="Reasoning").props("outlined dense options-dense").classes("w-full")
            enabled = ui.switch("Role enabled", value=cfg.enabled)
            use_llm = ui.switch("Use hosted model for this role", value=cfg.use_llm)
            ui.label(
                "Opt-in per role. The provider's subscription is tried first when enabled "
                "(ChatGPT/Codex for OpenAI, Claude login for Anthropic), then its optional API "
                "key. Typed Target data is sent to the provider; agents have no network or "
                "submission tools."
            ).classes("bh-dim")
            status = ui.label().classes("bh-dim")

            def _current_provider() -> ModelProvider:
                return ModelProvider(provider.value)

            def refresh_status() -> None:
                conn = ws.session.connection_for(_current_provider())
                status.set_text(conn.status)
                status.classes(
                    add="bh-ok" if conn.ready else "bh-bad",
                    remove="bh-bad bh-dim" if conn.ready else "bh-ok bh-dim",
                )

            def sync_provider(*, keep_model: bool) -> None:
                prov = _current_provider()
                presets = model_presets_for(prov)
                desired = model.value if keep_model and model.value else ""
                if not desired:
                    desired = default_model_for(prov)
                options = list(dict.fromkeys([*presets, desired])) if desired else list(presets)
                model.set_options(options, value=desired or (presets[0] if presets else None))
                refresh_status()

            def load_policy() -> None:
                assert ws.session.target is not None
                selected = ws.session.target.agent_config[role.value]
                provider.value = selected.provider.value
                model.set_options(
                    model_presets_for(selected.provider) or [selected.model],
                    value=selected.model,
                )
                reasoning.value = selected.reasoning.value
                enabled.value = selected.enabled
                use_llm.value = selected.use_llm
                sync_provider(keep_model=True)

            def apply_policy() -> None:
                assert ws.session.target is not None
                selected = ws.session.target.agent_config[role.value]
                prov = _current_provider()
                selected.provider = prov
                fallback = default_model_for(prov) or selected.model or "gpt-5.6-terra"
                selected.model = (model.value or fallback).strip() or fallback
                selected.reasoning = ReasoningLevel(reasoning.value)
                selected.enabled = bool(enabled.value)
                selected.use_llm = bool(use_llm.value)
                ws.session.mark_dirty()
                ui.notify(f"Updated {role.value} policy", type="info")

            provider.on_value_change(lambda _: sync_provider(keep_model=False))
            role.on_value_change(lambda _: load_policy())
            refresh_status()
            ui.button("Apply policy", on_click=apply_policy).props("unelevated dense no-caps")


def _surface_map(ws: Workspace) -> None:
    target = ws.session.target
    assert target is not None
    _section("Surface map", "Mapping emits bounded slices. Selecting a row opens an inspector tab.")
    columns = [
        {"name": "id", "label": "Slice", "field": "id", "align": "left"},
        {"name": "title", "label": "Surface", "field": "title", "align": "left"},
        {"name": "asset", "label": "In-scope asset", "field": "asset", "align": "left"},
        {"name": "kind", "label": "Kind", "field": "kind", "align": "left"},
        {"name": "status", "label": "Status", "field": "status", "align": "left"},
    ]
    rows = [{"id": s.id, "title": s.title, "asset": s.asset, "kind": s.kind.value, "status": s.status} for s in target.slices]

    def selected(event) -> None:
        if event.selection:
            _open_detail_tab(ws, "slice", event.selection[0]["id"])

    ui.table(columns=columns, rows=rows, row_key="id", selection="single", on_select=selected).props("dense flat square separator=cell").classes("w-full bh-table")
    if not rows:
        ui.label("No slices. Hunt → Start runs the configured Mapping role.").classes("bh-dim")


def _strategy_view(ws: Workspace) -> None:
    target = ws.session.target
    assert target is not None
    _section(
        "Oracle directives",
        "Oracle proposes strategy; the Organizer schedules focused work under ScopeGuard.",
    )
    ui.label("HYPOTHESES").classes("bh-subhead")
    if not target.hypotheses:
        ui.label("No hypotheses proposed.").classes("bh-dim")
    for hypothesis in target.hypotheses:
        with ui.column().classes("w-full gap-0 bh-list-row"):
            ui.label(f"{hypothesis.id} · {hypothesis.statement}")
            ui.label(
                f"{hypothesis.weakness.value} · {hypothesis.priority.value} · "
                f"{hypothesis.status.value} · slice {hypothesis.slice_id or 'pending map'}"
            ).classes("bh-dim")

    ui.label("CHAINS").classes("bh-subhead q-mt-sm")
    if not target.chains:
        ui.label("No chains proposed.").classes("bh-dim")
    for chain in target.chains:
        with ui.column().classes("w-full gap-0 bh-list-row"):
            ui.label(f"{chain.id} · {chain.impact}")
            ui.label(
                f"{' + '.join(chain.finding_ids)} · {chain.combined_severity.value} · "
                f"{chain.status.value}"
            ).classes("bh-dim")

    ui.label("ESCALATIONS").classes("bh-subhead q-mt-sm")
    if not target.escalations:
        ui.label("No escalations proposed.").classes("bh-dim")
    for escalation in target.escalations:
        with ui.column().classes("w-full gap-0 bh-list-row"):
            ui.label(f"{escalation.id} · {escalation.impact}")
            ui.label(
                f"{escalation.finding_id} · {escalation.from_severity.value} → "
                f"{escalation.to_severity.value} · {escalation.status.value}"
            ).classes("bh-dim")


def _slice_inspector(ws: Workspace, slice_id: str) -> None:
    target = ws.session.target
    assert target is not None
    surface = next((s for s in target.slices if s.id == slice_id), None)
    if surface is None:
        ui.label("SurfaceSlice no longer exists.").classes("bh-bad")
        return
    _section("Slice inspector", surface.title)
    _kv("Slice id", surface.id, mono=True)
    _kv("Asset", surface.asset, mono=True)
    _kv("Kind", surface.kind.value)
    _kv("Status", surface.status)
    _kv("Solver task", surface.solver_task_id or "not queued", mono=True)
    ui.label("NOTES").classes("bh-subhead q-mt-sm")
    ui.label(surface.notes)
    ui.label("One Solver receives this slice only.").classes("bh-dim")

    def queue() -> None:
        try:
            ws.organizer.queue_solver(surface.id)
            ui.notify(f"Queued one Solver for {surface.title}", type="positive")
        except Exception as exc:
            ui.notify(str(exc), type="negative")

    ui.button("Queue solver for this slice", icon="queue", on_click=queue).props("unelevated dense no-caps").classes("q-mt-sm")


def _findings_view(ws: Workspace) -> None:
    target = ws.session.target
    assert target is not None
    _section("Findings", "Candidates are shown only after Dedup and advocate gates.")
    columns = [
        {"name": "title", "label": "Finding", "field": "title", "align": "left"},
        {"name": "severity", "label": "Severity", "field": "severity", "align": "left"},
        {"name": "status", "label": "Status", "field": "status", "align": "left"},
        {"name": "verdict", "label": "Advocate verdict", "field": "verdict", "align": "left"},
    ]
    rows = [{"id": f.id, "title": f.title, "severity": f.severity.value, "status": f.status.value, "verdict": f.advocate_verdict} for f in target.findings]

    def selected(event) -> None:
        if event.selection:
            _open_detail_tab(ws, "finding", event.selection[0]["id"])

    ui.table(columns=columns, rows=rows, row_key="id", selection="single", on_select=selected).props("dense flat square separator=cell").classes("w-full bh-table")
    if not rows:
        ui.label("No findings yet.").classes("bh-dim")


def _finding_detail(ws: Workspace, finding_id: str) -> None:
    target = ws.session.target
    assert target is not None
    finding = next((f for f in target.findings if f.id == finding_id), None)
    if finding is None:
        ui.label("Finding no longer exists.").classes("bh-bad")
        return
    _section("Finding", finding.title)
    with ui.row().classes("gap-6"):
        _kv("Severity", finding.severity.value)
        _kv("Status", finding.status.value)
        _kv("Duplicate risk", finding.duplicate_risk)
    ui.label("SUMMARY").classes("bh-subhead q-mt-sm")
    ui.label(finding.summary)
    ui.label("DEVIL’S ADVOCATE").classes("bh-subhead q-mt-sm")
    ui.label(finding.advocate_verdict).classes("bh-bad" if finding.status is FindingStatus.KILLED else "bh-ok")
    if finding.killed_reason:
        ui.label(finding.killed_reason).classes("bh-dim")
    ui.label("EVIDENCE PATHS").classes("bh-subhead q-mt-sm")
    if finding.evidence:
        for path in finding.evidence:
            ui.label(path).classes("bh-mono")
    else:
        ui.label("No evidence attached.").classes("bh-dim")
    ui.label("VERIFIED REPRODUCTION STEPS").classes("bh-subhead q-mt-sm")
    if finding.reproduction_steps:
        for index, step in enumerate(finding.reproduction_steps, start=1):
            ui.label(f"{index}. {step}")
    else:
        ui.label("No verified reproduction steps.").classes("bh-dim")
    if finding.evidence_gaps:
        ui.label("EVIDENCE GAPS").classes("bh-subhead q-mt-sm")
        for gap in finding.evidence_gaps:
            ui.label(f"• {gap}").classes("bh-dim")


def _reports_view(ws: Workspace) -> None:
    target = ws.session.target
    assert target is not None
    _section("Report preview", REPORTER_NO_SUBMIT)
    if not target.reports:
        ui.label("No report preview yet.").classes("bh-dim")
        return
    for report in target.reports:
        with ui.row().classes("w-full items-center no-wrap bh-list-row"):
            ui.label(report.title).classes("flex-1")
            ui.label(report.platform.value).classes("bh-dim")
            ui.button("Open preview", on_click=lambda _=None, report_id=report.id: _open_detail_tab(ws, "report", report_id)).props("flat dense no-caps")


def _report_detail(ws: Workspace, report_id: str) -> None:
    target = ws.session.target
    assert target is not None
    report = next((r for r in target.reports if r.id == report_id), None)
    if report is None:
        ui.label("Report no longer exists.").classes("bh-bad")
        return
    _section("Report preview", "Export only · Bounty Hunter has no submit action")
    with ui.row().classes("items-center"):
        ui.button("Copy Markdown", icon="content_copy", on_click=lambda: ui.clipboard.write(report.markdown)).props("flat dense no-caps")
        ui.button("Download .md", icon="download", on_click=lambda: ui.download(report.markdown.encode("utf-8"), filename=f"{report.id}.md", media_type="text/markdown")).props("flat dense no-caps")
        ui.label("NEVER AUTO-SUBMITS").classes("bh-bad q-ml-sm")
    ui.markdown(report.markdown).classes("bh-report-preview")


def _rebuild_assistant(ws: Workspace) -> None:
    if ws.assistant_host is None:
        return
    ws.assistant_host.clear()
    session = ws.session
    with ws.assistant_host:
        ui.label("ASSISTANT").classes("bh-pane-head w-full")
        ui.label("Backend + log context · answers questions · does not hunt").classes("bh-panel-note")
        history = ui.column().classes("w-full flex-1 overflow-auto q-pa-sm gap-2")
        if not session.chat:
            session.chat.append(("assistant", "I can answer questions and summarize backend logs for the open Target. The Organizer owns all background hunt work."))

        def render() -> None:
            history.clear()
            with history:
                for who, message in session.chat:
                    with ui.column().classes(f"bh-message bh-message-{who} gap-0"):
                        ui.label("YOU" if who == "user" else "ASSISTANT").classes("bh-message-who")
                        ui.label(message).classes("bh-chat-msg")

        render()
        box = ui.textarea(placeholder="Message about the open Target…").props("outlined dense rows=2").classes("w-full q-px-xs bh-chat-input")
        box.set_enabled(session.target is not None)

        async def send() -> None:
            text = (box.value or "").strip()
            if not text or session.target is None:
                return
            box.value = ""
            deps = ws.organizer.deps
            if deps is None or deps.target.id != session.target.id:
                deps = HuntDeps(session=session, target=session.target, event_bus=session.event_bus, kill_switch=ws.organizer.kill_switch, hunt_id=session.gui.hunt_id)
            session.chat.append(("user", text))
            render()
            session.gui.assistant_busy = True
            try:
                reply = await assistant_chat(text, deps)
            except Exception as exc:
                detail = str(exc).strip()[:500] or type(exc).__name__
                reply = f"Model request failed: {detail}"
                session.log(reply, source="assistant", level=EventLevel.ERROR)
            finally:
                session.gui.assistant_busy = False
            session.chat.append(("assistant", reply))
            render()
            session.log("Assistant replied", source="assistant", level=EventLevel.AGENT)

        async def input_key(event) -> None:
            args = event.args if isinstance(event.args, dict) else {}
            if args.get("key") == "Enter" and args.get("ctrlKey"):
                await send()

        box.on("keydown", input_key, ["key", "ctrlKey"])
        with ui.row().classes("w-full items-center q-px-xs q-pb-xs"):
            ui.label("Ctrl+Enter").classes("bh-kbd")
            ui.space()
            ui.button("Send", icon="send", on_click=send).props("flat dense no-caps")
        if session.target is None:
            ui.label("Open a Target to enable chat.").classes("bh-dim q-px-sm q-pb-sm")


def _rebuild_roster(ws: Workspace) -> None:
    if ws.roster_host is None:
        return
    ws.roster_host.clear()
    target = ws.session.target
    with ws.roster_host:
        ui.label("AGENT ROSTER").classes("bh-pane-head w-full")
        if target is None:
            ui.label("No Target open").classes("bh-dim q-pa-sm")
            return
        for role in AgentRole:
            cfg = target.agent_config[role.value]
            with ui.row().classes("w-full no-wrap items-center bh-roster-row"):
                ui.icon("circle", size="8px").classes("bh-ok" if cfg.enabled else "bh-dim")
                with ui.column().classes("gap-0 flex-1"):
                    ui.label(role.value.title()).classes("bh-roster-name")
                    ui.label(ROLE_BLURBS[role]).classes("bh-roster-blurb")
                is_live = cfg.is_live
                if is_live:
                    provider_tag = PROVIDER_LABELS.get(cfg.provider, cfg.provider.value)
                    ready = ws.session.connection_for(cfg.provider).ready
                    mode = f"{provider_tag} · LIVE"
                else:
                    ready = False
                    mode = "stub"
                ui.label(f"{cfg.reasoning.value} · {mode}").classes(
                    "bh-ok" if ready else "bh-dim"
                )
