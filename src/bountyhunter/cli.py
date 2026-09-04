"""CLI: `bountyhunter` launches the workspace GUI."""

from __future__ import annotations

import typer

from bountyhunter import APP_NAME, __version__

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="Bounty Hunter — authorized bug bounty workbench.",
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    native: bool = typer.Option(False, "--native", help="Open a native window (pywebview)."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (localhost only by default)."),
    port: int = typer.Option(8088, "--port", help="HTTP port for the workspace."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev)."),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    _launch(host=host, port=port, native=native, reload=reload)


@app.command("gui")
def gui_cmd(
    native: bool = typer.Option(False, "--native"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8088, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Launch the workspace (same as `bountyhunter` with no args)."""
    _launch(host=host, port=port, native=native, reload=reload)


@app.command()
def version() -> None:
    """Print the version and exit."""
    typer.echo(f"{APP_NAME} {__version__}")


def _launch(*, host: str, port: int, native: bool, reload: bool) -> None:
    from bountyhunter.gui.app import run_gui

    run_gui(host=host, port=port, native=native, reload=reload)


def run() -> None:
    app()
