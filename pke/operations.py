"""Backup, restore, and local data operations."""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

from pke.config.settings import Settings
from pke.version import __version__


def export_backup(out: Path) -> None:
    """Export config and data files into a tar.gz archive."""
    settings = Settings.load()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    manifest = settings.data_dir / "manifest.json"
    manifest.write_text(
        json.dumps({"pke_version": __version__, "schema_version": 1}, indent=2),
        encoding="utf-8",
    )
    with tarfile.open(out, "w:gz") as archive:
        archive.add(settings.data_dir, arcname="data")
        if settings.config_path.exists():
            archive.add(settings.config_path, arcname="config.toml")


def import_backup(path: Path) -> None:
    """Import a tar.gz backup by replacing the data directory."""
    settings = Settings.load()
    staging = settings.data_dir.parent / f"{settings.data_dir.name}.import"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with tarfile.open(path, "r:gz") as archive:
        archive.extractall(staging)
    imported_data = staging / "data"
    if not imported_data.exists():
        raise ValueError("backup does not contain data/")
    backup = settings.data_dir.with_name(f"{settings.data_dir.name}.bak")
    if settings.data_dir.exists():
        if backup.exists():
            shutil.rmtree(backup)
        settings.data_dir.rename(backup)
    imported_data.rename(settings.data_dir)
    shutil.rmtree(staging)
