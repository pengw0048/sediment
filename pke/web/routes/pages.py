"""Server-rendered pages."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from pke.web.deps import templates


def router(store_getter: Any) -> APIRouter:
    """Build page routes."""
    api = APIRouter()

    @api.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse("/dashboard", status_code=307)

    @api.get("/dashboard")
    async def dashboard(request: Request):
        app = store_getter()
        evidence_count = app.sqlite.conn.execute(
            "SELECT count(*) AS n FROM evidence_events"
        ).fetchone()["n"]
        skill_count = app.sqlite.conn.execute("SELECT count(*) AS n FROM skill_nodes").fetchone()[
            "n"
        ]
        return templates.TemplateResponse(
            request,
            "today.html",
            {
                "evidence_count": evidence_count,
                "skill_count": skill_count,
                "locale": "en",
            },
        )

    @api.get("/review")
    async def review(request: Request):
        return templates.TemplateResponse(request, "review/index.html")

    @api.get("/skills")
    async def skills(request: Request):
        app = store_getter()
        rows = app.sqlite.conn.execute(
            "SELECT id, canonical_name, user_status FROM skill_nodes ORDER BY canonical_name"
        ).fetchall()
        return templates.TemplateResponse(
            request, "skills.html", {"skills": [dict(row) for row in rows]}
        )

    @api.get("/skills/{skill_id}")
    async def skill_detail(request: Request, skill_id: str):
        app = store_getter()
        row = app.sqlite.conn.execute(
            "SELECT * FROM skill_nodes WHERE id = ?", (skill_id,)
        ).fetchone()
        return templates.TemplateResponse(
            request, "skill_detail.html", {"skill": dict(row) if row else None}
        )

    @api.get("/evidence")
    async def evidence(request: Request):
        rows = store_getter().evidence.list(limit=100)
        return templates.TemplateResponse(request, "evidence.html", {"rows": rows})

    @api.get("/settings")
    async def settings(request: Request):
        return templates.TemplateResponse(request, "settings.html")

    @api.get("/onboarding")
    async def onboarding(request: Request):
        return templates.TemplateResponse(request, "onboarding.html")

    @api.get("/calibration")
    async def calibration(request: Request):
        rows = (
            store_getter()
            .sqlite.conn.execute(
                "SELECT * FROM calibration_log ORDER BY occurred_at DESC LIMIT 100"
            )
            .fetchall()
        )
        return templates.TemplateResponse(
            request,
            "calibration.html",
            {"rows": [dict(row) for row in rows]},
        )

    @api.get("/admin")
    async def admin(request: Request):
        rows = (
            store_getter()
            .sqlite.conn.execute("SELECT * FROM quality_metrics ORDER BY recorded_at DESC LIMIT 50")
            .fetchall()
        )
        return templates.TemplateResponse(
            request, "admin.html", {"rows": [dict(row) for row in rows]}
        )

    return api
