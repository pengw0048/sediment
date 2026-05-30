"""Generate local service manager files."""

from __future__ import annotations

import platform
from pathlib import Path


def install_service_file() -> Path:
    """Generate a systemd user unit or macOS LaunchAgent plist."""
    system = platform.system().lower()
    if system == "darwin":
        path = Path.home() / "Library" / "LaunchAgents" / "ai.pke.serve.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>ai.pke.serve</string>
<key>ProgramArguments</key><array><string>pke</string><string>serve</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
""",
            encoding="utf-8",
        )
        return path
    path = Path.home() / ".config" / "systemd" / "user" / "pke.service"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """[Unit]
Description=Sediment Personal Knowledge Engine

[Service]
ExecStart=pke serve
Restart=on-failure

[Install]
WantedBy=default.target
""",
        encoding="utf-8",
    )
    return path
