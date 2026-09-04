"""Launch the NiceGUI workspace."""

from __future__ import annotations

from bountyhunter import APP_NAME
from bountyhunter.session import Session


def run_gui(
    *,
    host: str = "127.0.0.1",
    port: int = 8088,
    native: bool = False,
    reload: bool = False,
    show: bool = True,
) -> None:
    from nicegui import ui

    from bountyhunter.gui.pages import build_workspace

    session = Session()

    @ui.page("/")
    def index() -> None:
        ui.page_title(APP_NAME)
        build_workspace(session)

    ui.run(
        host=host,
        port=port,
        title=APP_NAME,
        dark=True,
        native=native,
        window_size=(1400, 900) if native else None,
        reload=reload,
        show=show and not native,
        uvicorn_logging_level="warning",
    )
