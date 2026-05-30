"""Skill API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pke.review.feedback import already_known, drop_skill


def router(store_getter: Any) -> APIRouter:
    """Build skill API routes."""
    api = APIRouter(prefix="/api/v1/skills")

    @api.get("/{skill_id}")
    async def get_skill(skill_id: str) -> dict[str, Any]:
        row = (
            store_getter()
            .sqlite.conn.execute("SELECT * FROM skill_nodes WHERE id = ?", (skill_id,))
            .fetchone()
        )
        return dict(row) if row else {}

    @api.patch("/{skill_id}")
    async def patch_skill(skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        app = store_getter()
        status = payload.get("user_status")
        if status == "dropped":
            drop_skill(app.sqlite, skill_id)
        elif status == "already_known":
            already_known(app.sqlite, skill_id)
        return {"status": "ok"}

    return api
