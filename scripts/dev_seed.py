#!/usr/bin/env python3
"""Seed a local Sediment database with synthetic evidence."""

from pke.adapters.manual_cli import build_manual_event
from pke.app import App

if __name__ == "__main__":
    app = App.create()
    for idx in range(10):
        app.evidence.add(
            build_manual_event(user=f"I practiced async context managers example {idx}")
        )
    print("seeded")
