from __future__ import annotations

import asyncio

import pytest
from nicegui import Client, ui
from nicegui.page import page

from bountyhunter.gui.pages import build_workspace
from bountyhunter.models import Target
from bountyhunter.session import Session
from bountyhunter.settings import AppSettings


@pytest.mark.asyncio
async def test_background_session_refresh_reenters_client_slot(tmp_path, monkeypatch) -> None:
    """Organizer notifications run in tasks without NiceGUI's ambient slot stack."""
    # These functions need a running NiceGUI server, which is irrelevant to the
    # slot-stack regression this unit test exercises.
    monkeypatch.setattr(ui, "run_javascript", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ui, "page_title", lambda *_args, **_kwargs: None)

    session = Session(settings=AppSettings(), settings_path=tmp_path / "settings.json")
    session.new_unsaved(Target(name="UI", authorized=True, in_scope=["ui.test"]))
    client = Client(page("/background-slot-test"))
    with client:
        workspace = build_workspace(session)

    async def notify_from_background_task() -> None:
        session.notify()

    await asyncio.create_task(notify_from_background_task())
    await asyncio.sleep(0)

    # A notification can also originate inside a callback whose source lives
    # in a host that the refresh clears. The rebuild must wait until that
    # callback has finished using its source slot.
    with client:
        assert workspace.center_host is not None
        with workspace.center_host:
            callback_source = ui.column()
        with callback_source:
            session.notify()
            ui.label("callback continued after notify")

    await asyncio.sleep(0)
