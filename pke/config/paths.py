"""XDG-style path helpers for Sediment.

The module avoids network or platform services and only expands local paths.
"""

from __future__ import annotations

import os
from pathlib import Path


def config_home() -> Path:
    """Return the user configuration directory."""
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "pke"


def data_home() -> Path:
    """Return the user data directory."""
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "pke"


def default_config_path() -> Path:
    """Return the default config file path."""
    return config_home() / "config.toml"
