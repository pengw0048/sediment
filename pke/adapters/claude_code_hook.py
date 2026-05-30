"""Claude Code hook adapter and installer.

The hook command is intentionally thin: it reads stdin, posts to the local PKE
server, and buffers locally if the server is offline.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from pke.evidence.models import (
    EvidenceEvent,
    EvidenceModality,
    EvidenceRole,
    EvidenceTurn,
    sha256_hex,
    utc_now,
)

HOOK_SNIPPET = {
    "UserPromptSubmit": [
        {
            "matcher": ".*",
            "hooks": [{"type": "command", "command": "pke-claude-code-hook user_prompt"}],
        }
    ],
    "PostToolUse": [
        {
            "matcher": ".*",
            "hooks": [{"type": "command", "command": "pke-claude-code-hook post_tool_use"}],
        }
    ],
}


def install_settings_hook(settings_path: Path | None = None) -> Path:
    """Merge Claude Code hook config into ~/.claude/settings.json."""
    path = settings_path or (Path.home() / ".claude" / "settings.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any]
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        backup = path.with_name(f"{path.name}.bak.{int(time.time())}")
        backup.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    else:
        raw = {}
    hooks = raw.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        raw["hooks"] = hooks
    for event_name, entries in HOOK_SNIPPET.items():
        existing = hooks.setdefault(event_name, [])
        if not isinstance(existing, list):
            existing = []
            hooks[event_name] = existing
        commands = {
            hook.get("command")
            for entry in existing
            if isinstance(entry, dict)
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict)
        }
        for entry in entries:
            command = entry["hooks"][0]["command"]
            if command not in commands:
                existing.append(entry)
    path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    return path


def event_from_hook_envelope(envelope: dict[str, Any]) -> EvidenceEvent:
    """Convert one buffered hook envelope into an EvidenceEvent."""
    raw = envelope.get("raw", {})
    if not isinstance(raw, dict):
        raw = {}
    session_id = str(envelope.get("session_id") or raw.get("session_id") or "unknown")
    kind = str(envelope.get("kind") or "user_prompt")
    received_at = float(envelope.get("received_at") or utc_now())
    cwd = str(envelope.get("cwd") or raw.get("cwd") or "")
    if kind == "post_tool_use":
        tool_name = str(raw.get("tool_name") or raw.get("name") or "tool")
        tool_input = raw.get("tool_input") or raw.get("input") or raw.get("args") or {}
        tool_result = raw.get("tool_result") or raw.get("result") or ""
        turns = [
            EvidenceTurn(
                role=EvidenceRole.TOOL_RESULT,
                modality=EvidenceModality.TOOL_IO,
                content=json.dumps(tool_input, sort_keys=True),
                tool_name=tool_name,
                tool_args_json=json.dumps(tool_input, sort_keys=True),
            ),
            EvidenceTurn(
                role=EvidenceRole.TOOL_RESULT,
                modality=EvidenceModality.TOOL_IO,
                content=str(tool_result),
                tool_name=tool_name,
            ),
        ]
        turn_index = int(raw.get("turn_index") or 0)
    else:
        prompt = str(raw.get("prompt") or raw.get("message") or raw.get("text") or "")
        turns = [
            EvidenceTurn(role=EvidenceRole.USER, modality=EvidenceModality.TEXT, content=prompt)
        ]
        turn_index = int(raw.get("turn_index") or 0)
    external_id = sha256_hex(f"{session_id}:{turn_index}:{kind}:{json.dumps(raw, sort_keys=True)}")
    return EvidenceEvent(
        source="claude_code_hook",
        external_id=external_id,
        conversation_id=f"cc_{session_id}",
        turn_index=turn_index,
        occurred_at=float(raw.get("timestamp") or received_at),
        ingested_at=utc_now(),
        turns=turns,
        app="claude_code",
        model=str(raw.get("model")) if raw.get("model") else None,
        workspace=cwd or None,
        extra={"hook_kind": kind},
    )


def hook_main() -> int:
    """Entry point used by Claude Code hooks."""
    kind = sys.argv[1] if len(sys.argv) > 1 else "user_prompt"
    try:
        raw_text = sys.stdin.read()
        raw = json.loads(raw_text) if raw_text else {}
    except json.JSONDecodeError:
        return 0
    envelope = {
        "kind": kind,
        "received_at": utc_now(),
        "session_id": raw.get("session_id") if isinstance(raw, dict) else None,
        "cwd": raw.get("cwd") if isinstance(raw, dict) else os.getcwd(),
        "raw": raw,
    }
    base_url = os.environ.get("PKE_URL", "http://127.0.0.1:7421")
    try:
        import httpx

        httpx.post(
            f"{base_url}/internal/adapters/claude_code_hook/ingest",
            json=envelope,
            timeout=0.25,
        )
    except Exception:
        buffer_dir = Path(os.environ.get("PKE_HOME", str(Path.home() / ".pke"))) / "hook_buffer"
        buffer_dir.mkdir(parents=True, exist_ok=True)
        target = buffer_dir / f"{int(time.time() * 1000)}-{uuid.uuid4().hex}.json"
        target.write_text(json.dumps(envelope), encoding="utf-8")
    return 0
