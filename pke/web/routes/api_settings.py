"""Settings API routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter

from pke.evidence.models import iso_utc


def router(store_getter: Any) -> APIRouter:
    """Build settings API routes."""
    api = APIRouter(prefix="/api/v1/settings")

    @api.get("")
    async def get_settings() -> dict[str, Any]:
        rows = store_getter().sqlite.conn.execute("SELECT * FROM settings").fetchall()
        return {str(row["key"]): json.loads(str(row["value_json"])) for row in rows}

    @api.put("/{key}")
    async def put_setting(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        store_getter().sqlite.execute(
            "INSERT OR REPLACE INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(payload.get("value")), iso_utc()),
        )
        return {"status": "ok"}

    return api
