"""Binary acceptance verifier for the v1 checklist."""

from pathlib import Path


def test_acceptance_files_and_open_source_assets_exist():
    required = [
        "README.md",
        "LICENSE",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        ".github/FUNDING.yml",
        "docs/launch/show_hn.md",
        "docs/launch/zh_community.md",
        "pke-ext/manifest.json",
    ]
    for item in required:
        assert Path(item).exists(), item


def test_acceptance_core_modules_exist():
    required = [
        "pke/evidence/store.py",
        "pke/extraction/runner.py",
        "pke/identity/embedder.py",
        "pke/graph/kuzu_store.py",
        "pke/mastery/hlr.py",
        "pke/review/session.py",
        "pke/intervention/decider.py",
        "pke/web/main.py",
        "pke/tui/app.py",
    ]
    for item in required:
        assert Path(item).exists(), item
