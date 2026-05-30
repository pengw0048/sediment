"""Application container for Sediment.

The App owns settings, database connections, and the layer services used by
CLI, web, adapters, review, and maintenance jobs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from pke.config.settings import Settings
from pke.db.sqlite import SQLiteStore
from pke.evidence.store import EvidenceStore
from pke.extraction.llm_client import AnthropicClient, LLMClient, OpenAIClient
from pke.graph.kuzu_store import KuzuStore


@dataclass(kw_only=True, slots=True)
class App:
    """Dependency container shared by every surface."""

    settings: Settings
    sqlite: SQLiteStore
    evidence: EvidenceStore
    graph: KuzuStore
    llm_client: LLMClient | None = field(default=None)

    @classmethod
    def create(cls, *, settings: Settings | None = None) -> App:
        """Create an application container and initialize SQLite schema."""
        resolved = settings or Settings.load()
        sqlite = SQLiteStore(path=resolved.evidence_db_path)
        sqlite.initialize()
        graph = KuzuStore(root=resolved.data_dir / "graph")
        graph.ensure_schema()
        return cls(
            settings=resolved,
            sqlite=sqlite,
            evidence=EvidenceStore(sqlite=sqlite),
            graph=graph,
            llm_client=_resolve_llm_client(),
        )

    def close(self) -> None:
        """Close owned resources."""
        self.sqlite.close()


def _resolve_llm_client() -> LLMClient | None:
    """Pick an LLM client from environment variables, or ``None`` if unset.

    Order of preference: Anthropic if ``ANTHROPIC_API_KEY`` is set, then an
    OpenAI-compatible endpoint if ``PKE_LLM_BASE_URL`` or ``OPENAI_API_KEY``
    is set. Returns ``None`` when nothing is configured so unit tests and
    offline workflows keep working without an LLM.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    base_url = os.environ.get("PKE_LLM_BASE_URL")
    if base_url:
        model = os.environ.get("PKE_LLM_MODEL", "gpt-5-mini")
        api_key_env = os.environ.get("PKE_LLM_API_KEY_ENV")
        return OpenAIClient(
            model=model,
            api_key_env=api_key_env,
            base_url=base_url,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIClient()
    return None
