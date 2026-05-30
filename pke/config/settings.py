"""Typed settings for Sediment.

Settings are intentionally local-first: paths are on the user's machine and API
keys are referenced by environment variable names, never stored directly.
"""

from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pke.config.paths import data_home, default_config_path

DEFAULT_CONFIG = """# Sediment default config
[core]
data_dir          = "~/.local/share/pke"
log_level         = "INFO"
single_user_token = ""
bind_host         = "127.0.0.1"
bind_port         = 7421

[llm]
provider          = "anthropic"
model             = "claude-haiku-4-5"
api_key_env       = "ANTHROPIC_API_KEY"
prompt_cache      = true
daily_usd_budget  = 1.00
batch_size        = 8
max_concurrent    = 2

[llm.openai]
model             = "gpt-5-mini"
api_key_env       = "OPENAI_API_KEY"

[llm.local]
model_path        = ""
context_length    = 8192
enable_thinking   = false

[embedding]
model        = "nomic-ai/nomic-embed-text-v1.5"
dim          = 768
device       = "auto"
batch_size   = 32

[identity]
ann_M                = 32
ann_ef_construction  = 200
ann_ef_search        = 64
merge_threshold      = 0.86
candidate_threshold  = 0.74
denstream_lambda     = 0.0006
denstream_eps        = 0.18
denstream_beta       = 0.75
denstream_mu         = 2.0

[mastery]
review_items_per_day = 5
calibration_window   = 50
hlr_base_halflife_h  = 24

[intervention.defaults]
strength             = "quiet"
toast_cooldown_min   = 10
gentle_every_n       = 30

[intervention.per_source]
claude_code_hook = "quiet"
claude_code_tail = "off"
chatgpt_web      = "gentle"
claude_ai_web    = "gentle"
gemini_web       = "gentle"
cursor_tail      = "off"
openai_proxy     = "quiet"
anthropic_proxy  = "quiet"
manual_cli       = "off"
file_watcher     = "off"

[adapters.claude_code_tailer]
enabled = true
roots   = ["~/.claude/projects"]

[adapters.cursor]
enabled = true
roots   = ["~/.cursor/projects"]

[adapters.openai_proxy]
enabled = false
listen  = "127.0.0.1:7422"

[adapters.anthropic_proxy]
enabled = false
listen  = "127.0.0.1:7423"

[adapters.file_watcher]
enabled = true
inbox   = "~/.local/share/pke/inbox"

[jobs]
decay_cron           = "0 3 * * *"
audit_cron           = "30 3 * * 0"
reindex_cron         = "0 4 1 * *"
vacuum_cron          = "15 4 * * 0"
bertopic_cron        = "0 5 * * 0"
"""


@dataclass(kw_only=True, slots=True)
class Settings:
    """Resolved settings used by runtime components."""

    data_dir: Path
    config_path: Path
    bind_host: str = "127.0.0.1"
    bind_port: int = 7421
    single_user_token: str = ""
    llm_provider: str = "anthropic"
    llm_model: str = "claude-haiku-4-5"
    llm_api_key_env: str = "ANTHROPIC_API_KEY"
    llm_daily_usd_budget: float = 1.0
    embedding_model: str = "nomic-ai/nomic-embed-text-v1.5"
    embedding_dim: int = 768
    intervention_default: str = "quiet"
    intervention_per_source: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def evidence_db_path(self) -> Path:
        """Return the SQLite evidence database path."""
        return self.data_dir / "evidence.db"

    @property
    def graph_dir(self) -> Path:
        """Return the Kuzu-compatible graph directory."""
        return self.data_dir / "graph.kuzu"

    @property
    def inbox_dir(self) -> Path:
        """Return the drop-in file watcher inbox."""
        adapters = self.raw.get("adapters", {})
        watcher = adapters.get("file_watcher", {}) if isinstance(adapters, dict) else {}
        inbox = watcher.get("inbox", str(self.data_dir / "inbox"))
        return Path(str(inbox)).expanduser()

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        """Load settings from disk, falling back to defaults."""
        config_path = path or default_config_path()
        if config_path.exists():
            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        else:
            raw = tomllib.loads(DEFAULT_CONFIG)
        core = raw.get("core", {})
        llm = raw.get("llm", {})
        embedding = raw.get("embedding", {})
        intervention = raw.get("intervention", {})
        defaults = intervention.get("defaults", {}) if isinstance(intervention, dict) else {}
        per_source = intervention.get("per_source", {}) if isinstance(intervention, dict) else {}
        data_dir = Path(str(core.get("data_dir", data_home()))).expanduser()
        env_data = os.environ.get("PKE_CORE__DATA_DIR")
        if env_data:
            data_dir = Path(env_data).expanduser()
        return cls(
            data_dir=data_dir,
            config_path=config_path,
            bind_host=str(core.get("bind_host", "127.0.0.1")),
            bind_port=int(core.get("bind_port", 7421)),
            single_user_token=str(core.get("single_user_token", "")),
            llm_provider=str(llm.get("provider", "anthropic")),
            llm_model=str(llm.get("model", "claude-haiku-4-5")),
            llm_api_key_env=str(llm.get("api_key_env", "ANTHROPIC_API_KEY")),
            llm_daily_usd_budget=float(llm.get("daily_usd_budget", 1.0)),
            embedding_model=str(embedding.get("model", "nomic-ai/nomic-embed-text-v1.5")),
            embedding_dim=int(embedding.get("dim", 768)),
            intervention_default=str(defaults.get("strength", "quiet")),
            intervention_per_source={str(k): str(v) for k, v in per_source.items()},
            raw=raw,
        )

    @classmethod
    def init_files(cls, path: Path | None = None) -> Settings:
        """Create config and data directories without downloading models."""
        config_path = path or default_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if not config_path.exists():
            token = secrets.token_urlsafe(24)
            config_path.write_text(
                DEFAULT_CONFIG.replace('single_user_token = ""', f'single_user_token = "{token}"'),
                encoding="utf-8",
            )
        settings = cls.load(config_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.graph_dir.mkdir(parents=True, exist_ok=True)
        settings.inbox_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "models").mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "logs").mkdir(parents=True, exist_ok=True)
        return settings
