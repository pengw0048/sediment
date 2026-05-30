"""Concrete :class:`InputAdapter` implementations for every input source.

Each adapter module under :mod:`pke.adapters` exposes functions that
parse or watch one external surface. This module wraps those functions
in a small class that satisfies the :class:`InputAdapter` runtime
protocol — ``name``, ``version``, ``start``, ``stop``, ``events``,
``health``, ``backfill`` — so the daemon can iterate the registry and
treat every adapter the same way regardless of whether it is a
JSONL tailer, a watchdog inbox, a passive HTTP proxy, or a one-shot
archive importer.

``ALL_ADAPTERS`` is the registered list; ``register`` lets test code add
a new adapter without editing this module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from pke.adapters.base import AdapterConfig, AdapterState, InputAdapter, _AdapterBase
from pke.evidence.models import EvidenceEvent


@dataclass(kw_only=True, slots=True)
class AnthropicProxyAdapter(_AdapterBase):
    """Passive HTTP proxy in front of api.anthropic.com."""

    name: str = "anthropic_proxy"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class OpenAIProxyAdapter(_AdapterBase):
    """Passive HTTP proxy in front of api.openai.com and compatible servers."""

    name: str = "openai_proxy"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class BrowserExtensionAdapter(_AdapterBase):
    """FastAPI endpoint receiving events from the MV3 browser extension."""

    name: str = "browser_ext"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class ChatGPTHistoryAdapter(_AdapterBase):
    """One-shot importer for ChatGPT export archives."""

    name: str = "chatgpt_history"
    version: str = "0.1.0"

    async def backfill(self, *, since: float | None = None) -> AsyncIterator[EvidenceEvent]:
        """Importer is invoked from the CLI; this stays empty by default."""
        del since
        if False:
            yield


@dataclass(kw_only=True, slots=True)
class ClaudeAIHistoryAdapter(_AdapterBase):
    """One-shot importer for claude.ai export archives."""

    name: str = "claude_ai_history"
    version: str = "0.1.0"

    async def backfill(self, *, since: float | None = None) -> AsyncIterator[EvidenceEvent]:
        del since
        if False:
            yield


@dataclass(kw_only=True, slots=True)
class ClaudeCodeHookAdapter(_AdapterBase):
    """Receives JSON envelopes from the Claude Code hook installer."""

    name: str = "claude_code_hook"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class ClaudeCodeTailerAdapter(_AdapterBase):
    """Tails ~/.claude/transcripts/*.jsonl via watchdog."""

    name: str = "claude_code_tailer"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class CursorAdapter(_AdapterBase):
    """Reads Cursor's local transcript files."""

    name: str = "cursor"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class FileWatcherAdapter(_AdapterBase):
    """Drop-in inbox importer at ~/PKE/inbox/."""

    name: str = "file_watcher"
    version: str = "0.1.0"


@dataclass(kw_only=True, slots=True)
class ManualCLIAdapter(_AdapterBase):
    """`pke evidence add` manual entry."""

    name: str = "manual_cli"
    version: str = "0.1.0"


ALL_ADAPTERS: list[type] = [
    AnthropicProxyAdapter,
    OpenAIProxyAdapter,
    BrowserExtensionAdapter,
    ChatGPTHistoryAdapter,
    ClaudeAIHistoryAdapter,
    ClaudeCodeHookAdapter,
    ClaudeCodeTailerAdapter,
    CursorAdapter,
    FileWatcherAdapter,
    ManualCLIAdapter,
]
"""Every concrete adapter, used by the registry test to enforce coverage."""


def register(adapter_cls: type) -> None:
    """Append ``adapter_cls`` to the registry list."""
    ALL_ADAPTERS.append(adapter_cls)


__all__ = [
    "ALL_ADAPTERS",
    "AdapterConfig",
    "AdapterState",
    "AnthropicProxyAdapter",
    "BrowserExtensionAdapter",
    "ChatGPTHistoryAdapter",
    "ClaudeAIHistoryAdapter",
    "ClaudeCodeHookAdapter",
    "ClaudeCodeTailerAdapter",
    "CursorAdapter",
    "FileWatcherAdapter",
    "InputAdapter",
    "ManualCLIAdapter",
    "OpenAIProxyAdapter",
    "register",
]
