"""File dialogs: New / Open / Save As / dirty confirm / about."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from nicegui import ui

from bountyhunter.models import Platform, Target, parse_scope_text, utcnow
from bountyhunter.safety import ATTESTATION, AuthorizationError
from bountyhunter.session import Session
from bountyhunter.settings import save_settings
from bountyhunter.store import TargetStoreError, default_target_dir


def _notify_err(exc: BaseException) -> None:
    ui.notify(str(exc), type="negative", timeout=4000)


def new_target_dialog(session: Session, on_done: Callable[[], None] | None = None) -> None:
    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().classes("w-[640px] max-w-[92vw] max-h-[90vh] overflow-auto").tight():
        ui.label("New Target").classes("text-base font-medium q-pa-sm")
        ui.separator()
        with ui.column().classes("q-pa-md w-full gap-2"):
            ui.label(
                "A Target is the project — like a Burp project file. "
                "It stores scope, slices, findings, evidence, and reports."
            ).classes("bh-dim")
            name = ui.input("Name", placeholder="Example Corp").props("dense outlined").classes("w-full")
            platform = ui.select(
                {p.value: p.value.replace("_", " ").title() for p in Platform},
                label="Platform",
                value=Platform.HACKERONE.value,
            ).props("dense outlined").classes("w-full")
            url = ui.input("Program URL (optional)", placeholder="https://hackerone.com/example").props(
                "dense outlined"
            ).classes("w-full")
            inscope = ui.textarea(
                "In scope (one asset per line)",
                placeholder="*.example.com\nhttps://api.example.com",
            ).props("dense outlined autogrow").classes("w-full")
            outscope = ui.textarea(
                "Out of scope (one asset per line)",
                placeholder="blog.example.com\nmarketing.example.com",
            ).props("dense outlined autogrow").classes("w-full")
            ui.label("The new Target is unsaved until File → Save / Save Target As…").classes(
                "bh-dim text-xs"
            )

            ui.separator()
            authorized = ui.checkbox(ATTESTATION)
            ui.label("Required. Bounty Hunter will not create a Target without it.").classes(
                "bh-bad text-xs"
            )

        with ui.row().classes("q-pa-sm w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def submit() -> None:
                try:
                    if not authorized.value:
                        raise AuthorizationError("Authorization checkbox is required to create a Target.")
                    n = (name.value or "").strip()
                    if not n:
                        raise ValueError("Name is required")
                    in_scope = parse_scope_text(inscope.value or "")
                    out_of_scope = parse_scope_text(outscope.value or "")
                    target = Target(
                        name=n,
                        platform=Platform(platform.value),
                        program_url=(url.value or "").strip(),
                        in_scope=in_scope,
                        out_of_scope=out_of_scope,
                        authorized=True,
                        authorization_attestation=ATTESTATION,
                        authorized_at=utcnow(),
                        continuous_hunt=True,
                    )
                    session.new_unsaved(target)
                    dialog.close()
                    if on_done:
                        on_done()
                    ui.notify(f"Target «{n}» ready", type="positive")
                except (AuthorizationError, TargetStoreError, ValueError) as exc:
                    _notify_err(exc)

            ui.button("Create Target", on_click=submit).props("unelevated")

    dialog.open()


def open_target_dialog(session: Session, on_done: Callable[[], None] | None = None) -> None:
    dialog = ui.dialog()
    with dialog, ui.card().classes("w-[560px] max-w-[92vw]"):
        ui.label("Open Target").classes("text-base font-medium")
        ui.label("Target folder, target.json, or .bountyhunt.json.").classes("bh-dim")
        path = ui.input("Path").props("dense outlined").classes("w-full")
        if session.settings.recent:
            ui.label("Recent").classes("text-xs bh-dim q-mt-sm")
            for item in session.settings.recent:
                def _open_recent(p=item.path) -> None:
                    path.value = p
                    try_open()

                with ui.row().classes("items-center w-full no-wrap cursor-pointer"):
                    ui.button(
                        f"{item.name}  —  {item.path}",
                        on_click=_open_recent,
                    ).props("flat dense no-caps").classes("w-full text-left")

        def try_open() -> None:
            raw = (path.value or "").strip()
            if not raw:
                ui.notify("Enter a path", type="warning")
                return
            try:
                session.open_path(Path(raw))
                dialog.close()
                if on_done:
                    on_done()
                ui.notify("Opened", type="positive")
            except Exception as exc:
                _notify_err(exc)

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Open", on_click=try_open).props("unelevated")
    dialog.open()


def save_as_dialog(session: Session, on_done: Callable[[], None] | None = None) -> None:
    if session.target is None:
        ui.notify("No target to save", type="warning")
        return
    suggested = session.path or default_target_dir(session.target.name)
    dialog = ui.dialog()
    with dialog, ui.card().classes("w-[520px]"):
        ui.label("Save Target As…").classes("text-base font-medium")
        ui.label("Choose a folder. Bounty Hunter writes target.json and its artifact folders.").classes("bh-dim")
        path = ui.input("Folder", value=str(suggested)).props("dense outlined").classes("w-full")

        def submit() -> None:
            raw = (path.value or "").strip()
            if not raw:
                ui.notify("Enter a folder", type="warning")
                return
            try:
                session.save_as(Path(raw))
                dialog.close()
                if on_done:
                    on_done()
                ui.notify("Saved", type="positive")
            except Exception as exc:
                _notify_err(exc)

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=submit).props("unelevated")
    dialog.open()


def dirty_dialog(
    session: Session,
    *,
    then: Callable[[], None],
    title: str = "Unsaved changes",
) -> None:
    name = session.target.name if session.target else "target"
    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().classes("w-[420px]"):
        ui.label(title).classes("text-base font-medium")
        ui.label(f"Save changes to «{name}» before continuing?").classes("bh-dim")

        def save_then() -> None:
            try:
                if session.path is None:
                    dialog.close()
                    save_as_dialog(session, on_done=then)
                    return
                session.save()
                dialog.close()
                then()
            except Exception as exc:
                _notify_err(exc)

        def discard() -> None:
            dialog.close()
            then()

        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Don't Save", on_click=discard).props("flat")
            ui.button("Save", on_click=save_then).props("unelevated")
    dialog.open()


def confirm_if_dirty(session: Session, then: Callable[[], None]) -> None:
    if session.dirty:
        dirty_dialog(session, then=then)
    else:
        then()


def about_dialog() -> None:
    dialog = ui.dialog()
    with dialog, ui.card().classes("w-[480px]"):
        ui.label("Bounty Hunter").classes("text-lg font-medium bh-accent")
        ui.label("Authorized bug bounty workbench.").classes("bh-dim")
        ui.markdown(
            "- A **Target** is the project (New / Open / Save).\n"
            "- Assistant stays in chat. Organizer runs the hunt.\n"
            "- Reporter **never auto-submits**.\n"
            "- Authorization is required to create a Target."
        )
        ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def shortcuts_dialog() -> None:
    dialog = ui.dialog()
    with dialog, ui.card().classes("w-[420px]"):
        ui.label("Keyboard shortcuts").classes("text-base font-medium")
        rows = [
            ("Ctrl+N", "New Target"),
            ("Ctrl+O", "Open Target"),
            ("Ctrl+S", "Save"),
            ("Ctrl+Shift+S", "Save Target As"),
            ("Ctrl+W", "Close Target"),
            ("Ctrl+Q", "Quit"),
            ("Ctrl+Enter", "Send Assistant message"),
        ]
        for k, v in rows:
            with ui.row().classes("w-full justify-between"):
                ui.label(v)
                ui.label(k).classes("bh-dim")
        ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def openai_connection_dialog(
    session: Session,
    on_done: Callable[[], None] | None = None,
) -> None:
    """Configure subscription-first OpenAI access and an optional API fallback."""
    dialog = ui.dialog()
    with dialog, ui.card().classes("w-[560px] max-w-[92vw]"):
        ui.label("OpenAI connection").classes("text-base font-medium")
        status = ui.label(session.openai.status).classes(
            "bh-ok" if session.openai.ready else "bh-bad"
        )
        prefer_subscription = ui.switch(
            "Use ChatGPT subscription first",
            value=session.openai.use_subscription_first,
        )
        subscription = ui.label(
            f"Codex login: {session.openai.codex_auth_method}"
        ).classes("bh-dim text-xs")
        ui.label(
            "Uses the existing `codex login` session and included ChatGPT/Codex usage. "
            "If it is unavailable, Bounty Hunter falls back to the API key below."
        ).classes("bh-dim")
        ui.separator()
        ui.label("Optional metered API fallback").classes("bh-subhead")
        ui.label(
            "Keys entered here stay in memory for this process only. They are never written "
            "to target.json, settings.json, events, prompts, or logs."
        ).classes("bh-dim")
        key = ui.input("Session API key", password=True, password_toggle_button=True).props(
            "dense outlined autocomplete=off"
        ).classes("w-full")
        ui.label("You can instead set OPENAI_API_KEY before launching bountyhunter.").classes(
            "bh-dim text-xs"
        )

        def refresh_status() -> None:
            status.set_text(session.openai.status)
            status.classes(
                add="bh-ok" if session.openai.ready else "bh-bad",
                remove="bh-bad" if session.openai.ready else "bh-ok",
            )
            subscription.set_text(f"Codex login: {session.openai.codex_auth_method}")
            if on_done:
                on_done()

        def set_preference() -> None:
            selected = bool(prefer_subscription.value)
            session.openai.use_subscription_first = selected
            session.settings.openai_subscription_first = selected
            save_settings(session.settings, session.settings_path)
            if selected:
                session.openai.codex_authenticated = None
                session.openai.codex_auth_method = "not checked"
            refresh_status()

        async def refresh_subscription() -> None:
            await asyncio.to_thread(session.openai.refresh_subscription)
            refresh_status()
            if not session.openai.codex_authenticated:
                ui.notify("Run `codex login` in a terminal, then refresh", type="warning")

        def apply_session_key() -> None:
            raw = key.value or ""
            if not raw.strip():
                ui.notify("Enter an API key", type="warning")
                return
            session.openai.use_session_key(raw)
            key.value = ""
            session.log("OpenAI API fallback configured", source="workspace")
            refresh_status()
            ui.notify("OpenAI API fallback ready", type="positive")

        def use_environment() -> None:
            session.openai.reload_environment()
            session.log("Reloaded OpenAI credential from environment", source="workspace")
            refresh_status()
            if not session.openai.configured:
                ui.notify("OPENAI_API_KEY is not set", type="warning")

        prefer_subscription.on_value_change(lambda _: set_preference())
        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("Refresh Codex login", on_click=refresh_subscription).props(
                "flat dense no-caps"
            )
            ui.button("Reload environment", on_click=use_environment).props("flat dense no-caps")
            ui.button("Close", on_click=dialog.close).props("flat dense no-caps")
            ui.button("Use session key", on_click=apply_session_key).props(
                "unelevated dense no-caps"
            )
    dialog.open()


def anthropic_connection_dialog(
    session: Session,
    on_done: Callable[[], None] | None = None,
) -> None:
    """Configure subscription-first Claude access and an optional API fallback."""
    dialog = ui.dialog()
    with dialog, ui.card().classes("w-[560px] max-w-[92vw]"):
        ui.label("Anthropic (Claude) connection").classes("text-base font-medium")
        status = ui.label(session.anthropic.status).classes(
            "bh-ok" if session.anthropic.ready else "bh-bad"
        )
        prefer_subscription = ui.switch(
            "Use Claude subscription first",
            value=session.anthropic.use_subscription_first,
        )
        subscription = ui.label(
            f"Claude login: {session.anthropic.claude_auth_method}"
        ).classes("bh-dim text-xs")
        ui.label(
            "Uses the existing `claude` (Claude Code) login and included Claude Pro/Max usage. "
            "If it is unavailable, Bounty Hunter falls back to the API key below."
        ).classes("bh-dim")
        ui.separator()
        ui.label("Optional metered API fallback").classes("bh-subhead")
        ui.label(
            "Keys entered here stay in memory for this process only. They are never written "
            "to target.json, settings.json, events, prompts, or logs."
        ).classes("bh-dim")
        key = ui.input("Session API key", password=True, password_toggle_button=True).props(
            "dense outlined autocomplete=off"
        ).classes("w-full")
        ui.label("You can instead set ANTHROPIC_API_KEY before launching bountyhunter.").classes(
            "bh-dim text-xs"
        )

        def refresh_status() -> None:
            status.set_text(session.anthropic.status)
            status.classes(
                add="bh-ok" if session.anthropic.ready else "bh-bad",
                remove="bh-bad" if session.anthropic.ready else "bh-ok",
            )
            subscription.set_text(f"Claude login: {session.anthropic.claude_auth_method}")
            if on_done:
                on_done()

        def set_preference() -> None:
            selected = bool(prefer_subscription.value)
            session.anthropic.use_subscription_first = selected
            session.settings.anthropic_subscription_first = selected
            save_settings(session.settings, session.settings_path)
            if selected:
                session.anthropic.mark_subscription_unchecked()
            refresh_status()

        async def refresh_subscription() -> None:
            await asyncio.to_thread(session.anthropic.refresh_subscription)
            refresh_status()
            if not session.anthropic.claude_authenticated:
                ui.notify("Run `claude auth login` in a terminal, then refresh", type="warning")

        def apply_session_key() -> None:
            raw = key.value or ""
            if not raw.strip():
                ui.notify("Enter an API key", type="warning")
                return
            session.anthropic.use_session_key(raw)
            key.value = ""
            session.log("Anthropic API fallback configured", source="workspace")
            refresh_status()
            ui.notify("Anthropic API fallback ready", type="positive")

        def use_environment() -> None:
            session.anthropic.reload_environment()
            session.log("Reloaded Anthropic credential from environment", source="workspace")
            refresh_status()
            if not session.anthropic.configured:
                ui.notify("ANTHROPIC_API_KEY is not set", type="warning")

        prefer_subscription.on_value_change(lambda _: set_preference())
        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("Refresh Claude login", on_click=refresh_subscription).props(
                "flat dense no-caps"
            )
            ui.button("Reload environment", on_click=use_environment).props("flat dense no-caps")
            ui.button("Close", on_click=dialog.close).props("flat dense no-caps")
            ui.button("Use session key", on_click=apply_session_key).props(
                "unelevated dense no-caps"
            )
    dialog.open()
