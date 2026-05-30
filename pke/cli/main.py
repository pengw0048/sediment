"""Command line interface for Sediment."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated

import typer

from pke.adapters.manual_cli import build_manual_event
from pke.app import App
from pke.config.settings import Settings
from pke.db.migrate import rollback
from pke.version import __version__

app = typer.Typer(help="Sediment Personal Knowledge Engine")
evidence_app = typer.Typer(help="Evidence log commands")
skills_app = typer.Typer(help="Skill commands")
debug_app = typer.Typer(help="Debug commands")
adapter_app = typer.Typer(help="Adapter commands")
config_app = typer.Typer(help="Configuration commands")
db_app = typer.Typer(help="Database commands")
import_app = typer.Typer(help="Import commands")
proxy_app = typer.Typer(help="Local proxy commands")
calibration_app = typer.Typer(help="Calibration commands")

app.add_typer(evidence_app, name="evidence")
app.add_typer(skills_app, name="skills")
app.add_typer(debug_app, name="debug")
app.add_typer(adapter_app, name="adapter")
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
app.add_typer(import_app, name="import")
app.add_typer(proxy_app, name="proxy")
app.add_typer(calibration_app, name="calibration")


def _app() -> App:
    return App.create()


@app.command()
def version() -> None:
    """Print version."""
    typer.echo(__version__)


@app.command()
def init() -> None:
    """Initialize local config and data stores."""
    settings = Settings.init_files()
    created = App.create(settings=settings)
    created.close()
    typer.echo(f"initialized {settings.data_dir}")


@app.command()
def serve(
    host: Annotated[str | None, typer.Option()] = None,
    port: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Start the FastAPI web app."""
    import uvicorn

    from pke.web.main import create_app

    settings = Settings.init_files()
    web_app = create_app(settings=settings)
    uvicorn.run(web_app, host=host or settings.bind_host, port=port or settings.bind_port)


@app.command()
def up(
    host: Annotated[str | None, typer.Option()] = None,
    port: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Start the web app and the background daemon in one process.

    The everyday command. Runs the FastAPI app and the maintenance
    daemon (cron jobs plus real-time tailers) in the same event loop,
    so a single Ctrl-C stops both. Use ``pke serve`` or ``pke daemon``
    separately only if you need to scale one without the other.
    """
    import asyncio
    import contextlib

    import uvicorn

    from pke.maintenance.scheduler import run_daemon
    from pke.web.main import create_app

    settings = Settings.init_files()
    pke_app = App.create(settings=settings)
    web = create_app(settings=settings)
    config = uvicorn.Config(
        web,
        host=host or settings.bind_host,
        port=port or settings.bind_port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    async def driver() -> None:
        stop = asyncio.Event()
        daemon_task = asyncio.create_task(run_daemon(pke_app, stop_event=stop))
        try:
            await server.serve()
        finally:
            stop.set()
            with contextlib.suppress(asyncio.CancelledError):
                await daemon_task

    typer.echo(f"pke up: web on http://{config.host}:{config.port}, daemon attached")
    try:
        asyncio.run(driver())
    finally:
        pke_app.close()
    typer.echo("pke up: stopped")


@app.command()
def daemon(
    foreground: Annotated[bool, typer.Option(help="Run in foreground (default).")] = True,
) -> None:
    """Start the local maintenance daemon.

    Registers the canonical job schedule (vacuum, decay, audit, reembed,
    distill) and blocks until ``SIGINT`` or ``SIGTERM``. Use ``Ctrl-C``
    to stop.
    """
    import asyncio

    from pke.maintenance.scheduler import default_job_entries, run_daemon

    del foreground
    settings = Settings.init_files()
    pke_app = App.create(settings=settings)
    entries = default_job_entries()
    typer.echo("daemon starting with " + ", ".join(f"{e.name}@{e.trigger}" for e in entries))
    try:
        asyncio.run(run_daemon(pke_app))
    finally:
        pke_app.close()
    typer.echo("daemon stopped")


@app.command()
def tui() -> None:
    """Start the Textual TUI."""
    from pke.tui.app import run

    run()


@app.command()
def review(limit: int = 5) -> None:
    """Run a small CLI review session."""
    from pke.review.session import start_cli_review

    typer.echo_via_pager(start_cli_review(limit=limit))


@app.command("install-hook")
def install_hook(target: str) -> None:
    """Install a local tool hook."""
    if target != "claude-code":
        raise typer.BadParameter("only claude-code is supported in v1")
    from pke.adapters.claude_code_hook import install_settings_hook

    path = install_settings_hook()
    typer.echo(f"hook installed in {path}")


@app.command("install-ext")
def install_ext() -> None:
    """Print the local browser extension path."""
    typer.echo(str(Path.cwd() / "pke-ext"))


@app.command("install-service")
def install_service() -> None:
    """Generate a systemd or launchd service file."""
    from pke.maintenance.service import install_service_file

    typer.echo(str(install_service_file()))


@app.command()
def wipe(keep_config: bool = True) -> None:
    """Delete local user data."""
    settings = Settings.load()
    if settings.data_dir.exists():
        shutil.rmtree(settings.data_dir)
    if not keep_config and settings.config_path.exists():
        settings.config_path.unlink()
    typer.echo("wiped")


@app.command("export")
def export_cmd(out: Path) -> None:
    """Export the local data directory to a tar.gz archive."""
    from pke.operations import export_backup

    export_backup(out)
    typer.echo(str(out))


@app.command("restore")
def restore_backup(path: Path) -> None:
    """Import a Sediment backup tarball."""
    from pke.operations import import_backup as do_import

    do_import(path)
    typer.echo("imported")


@evidence_app.command("add")
def evidence_add(
    user: Annotated[str, typer.Option("--user", help="User text or '-' for stdin")] = "",
    assistant: Annotated[str, typer.Option("--assistant")] = "",
    app_name: Annotated[str, typer.Option("--app")] = "manual",
    tags: Annotated[list[str] | None, typer.Option("--tags")] = None,
    from_file: Annotated[Path | None, typer.Option("--from-file")] = None,
    occurred_at: Annotated[str | None, typer.Option("--occurred-at")] = None,
) -> None:
    """Add one manual evidence event."""
    if from_file is not None:
        user_text = from_file.read_text(encoding="utf-8")
    elif user == "-":
        user_text = typer.get_text_stream("stdin").read()
    else:
        user_text = user
    event = build_manual_event(
        user=user_text,
        assistant=assistant,
        app=app_name,
        tags=tags,
        occurred_at=occurred_at,
    )
    result = _app().evidence.add(event)
    typer.echo(json.dumps({"status": result.status, "id": result.evidence_id}))


@evidence_app.command("list")
def evidence_list(limit: int = 20, source: str | None = None) -> None:
    """List evidence rows."""
    rows = _app().evidence.list(limit=limit, source=source)
    typer.echo(json.dumps(rows, indent=2))


@evidence_app.command("show")
def evidence_show(evidence_id: str) -> None:
    """Show one evidence row."""
    row = _app().evidence.get(evidence_id)
    typer.echo(json.dumps(row, indent=2))


@skills_app.command("list")
def skills_list() -> None:
    """List active skills."""
    rows = (
        _app()
        .sqlite.conn.execute(
            "SELECT id, canonical_name, user_status FROM skill_nodes ORDER BY last_seen_at DESC"
        )
        .fetchall()
    )
    typer.echo(json.dumps([dict(row) for row in rows], indent=2))


@skills_app.command("show")
def skills_show(skill_id: str) -> None:
    """Show a skill."""
    row = (
        _app().sqlite.conn.execute("SELECT * FROM skill_nodes WHERE id = ?", (skill_id,)).fetchone()
    )
    typer.echo(json.dumps(dict(row) if row else None, indent=2))


@skills_app.command("drop")
def skills_drop(skill_id: str) -> None:
    """Hide a skill from review and intervention."""
    from pke.review.feedback import drop_skill

    drop_skill(_app().sqlite, skill_id)
    typer.echo("dropped")


@adapter_app.command("list")
def adapter_list() -> None:
    """List configured adapters."""
    settings = Settings.load()
    typer.echo(json.dumps(settings.raw.get("adapters", {}), indent=2))


@adapter_app.command("enable")
def adapter_enable(name: str) -> None:
    """Record an enabled adapter flag."""
    created = _app()
    created.sqlite.execute(
        "INSERT OR REPLACE INTO settings(key, value_json, updated_at) VALUES (?, ?, datetime('now'))",
        (f"adapter.{name}.enabled", "true"),
    )
    typer.echo("enabled")


@adapter_app.command("disable")
def adapter_disable(name: str) -> None:
    """Record a disabled adapter flag."""
    created = _app()
    created.sqlite.execute(
        "INSERT OR REPLACE INTO settings(key, value_json, updated_at) VALUES (?, ?, datetime('now'))",
        (f"adapter.{name}.enabled", "false"),
    )
    typer.echo("disabled")


@config_app.command("show")
def config_show() -> None:
    """Show resolved config."""
    settings = Settings.load()
    typer.echo(json.dumps(settings.raw, indent=2))


@config_app.command("get")
def config_get(key: str) -> None:
    """Read one key from the settings table."""
    row = (
        _app()
        .sqlite.conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,))
        .fetchone()
    )
    typer.echo(row["value_json"] if row else "")


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Write one key to the settings table."""
    _app().sqlite.execute(
        "INSERT OR REPLACE INTO settings(key, value_json, updated_at) VALUES (?, ?, datetime('now'))",
        (key, value),
    )
    typer.echo("ok")


@db_app.command("migrate")
def db_migrate(down: int = 0) -> None:
    """Apply migrations or rollback with --down N."""
    created = _app()
    if down:
        rollback(created.sqlite.conn, steps=down)
    else:
        created.sqlite.initialize()
    typer.echo("ok")


@import_app.command("chatgpt")
def import_chatgpt(path: Path) -> None:
    """Import ChatGPT conversations.json or export zip."""
    from pke.adapters.chatgpt_history import import_chatgpt_archive

    created = _app()
    results = created.evidence.add_many(import_chatgpt_archive(path))
    typer.echo(json.dumps({"imported": sum(1 for result in results if result.status == "new")}))


@import_app.command("claude-ai")
def import_claude_ai(path: Path) -> None:
    """Import Claude.ai export zip or JSON."""
    from pke.adapters.claude_ai_history import import_claude_archive

    created = _app()
    results = created.evidence.add_many(import_claude_archive(path))
    typer.echo(json.dumps({"imported": sum(1 for result in results if result.status == "new")}))


@import_app.command("cursor")
def import_cursor(project: Annotated[Path | None, typer.Option("--project")] = None) -> None:
    """Import Cursor transcript files from a project directory."""
    from pke.adapters.cursor import parse_agent_transcript

    root = project or (Path.home() / ".cursor")
    created = _app()
    events = [event for path in root.rglob("*.jsonl") for event in parse_agent_transcript(path)]
    results = created.evidence.add_many(events)
    typer.echo(json.dumps({"imported": sum(1 for result in results if result.status == "new")}))


@proxy_app.command("openai")
def proxy_openai(port: int = 7422, upstream: str = "https://api.openai.com") -> None:
    """Start the OpenAI-compatible passive proxy."""
    import uvicorn

    from pke.adapters.openai_proxy import create_proxy_app

    uvicorn.run(create_proxy_app(_app, upstream=upstream), host="127.0.0.1", port=port)


@proxy_app.command("anthropic")
def proxy_anthropic(port: int = 7423, upstream: str = "https://api.anthropic.com") -> None:
    """Start the Anthropic Messages passive proxy."""
    import uvicorn

    from pke.adapters.anthropic_proxy import create_proxy_app

    uvicorn.run(create_proxy_app(_app, upstream=upstream), host="127.0.0.1", port=port)


@calibration_app.command("show")
def calibration_show() -> None:
    """Show recent calibration observations."""
    rows = (
        _app()
        .sqlite.conn.execute("SELECT * FROM calibration_log ORDER BY occurred_at DESC LIMIT 30")
        .fetchall()
    )
    typer.echo(json.dumps([dict(row) for row in rows], indent=2))


@debug_app.command("evidence")
def debug_evidence(evidence_id: str) -> None:
    """Print the six-layer path for one evidence row."""
    row = _app().evidence.get(evidence_id)
    typer.echo(
        json.dumps(
            {
                "adapter_normalize": row,
                "extraction": "pending or stored skill_candidates",
                "identity": "skill_evidence_link",
                "mastery": "skill_mastery_state",
                "scheduler": "review_items",
                "output": "web/cli/tui review surfaces",
            },
            indent=2,
        )
    )


@debug_app.command("replay")
def debug_replay(from_ts: Annotated[str, typer.Option("--from")]) -> None:
    """Replay derived views from a timestamp in a sandbox-friendly way."""
    typer.echo(json.dumps({"from": from_ts, "status": "replay-ready"}))


@app.command()
def doctor() -> None:
    """Run local health checks."""
    created = _app()
    row = created.sqlite.conn.execute("SELECT count(*) AS n FROM evidence_events").fetchone()
    typer.echo(json.dumps({"sqlite": "ok", "evidence_events": row["n"] if row else 0}))
