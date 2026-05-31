"""Server-rendered pages."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from pke.mastery.selector import ItemSelector
from pke.web.deps import templates

# Rough cost of a single review item. Used to render the dashboard's
# "today's queue ~M minutes" pill. Two minutes is an honest average for
# the current item mix (read prompt, attempt unaided, type answer, see
# feedback). We do not estimate per-skill yet — that needs grader-kind
# timing data we have not collected.
MINUTES_PER_ITEM = 2

# Columns the /skills page can sort by, mapped to a SQL ORDER BY
# fragment. Keeping this server-side prevents SQL injection from the
# ``?sort=`` query parameter and keeps the canonical list of sortable
# columns in one place.
SKILL_SORT_COLUMNS: dict[str, str] = {
    "slippage": (
        "slippage_score DESC, "
        "CASE WHEN unaided_last_review_at IS NULL THEN 0 ELSE 1 END ASC, "
        "unaided_last_review_at ASC, "
        "canonical_name ASC"
    ),
    "name": "canonical_name ASC",
    "unaided": (
        "unaided_mastery ASC, slippage_score DESC, canonical_name ASC"
    ),
    "functional": (
        "functional_mastery ASC, slippage_score DESC, canonical_name ASC"
    ),
    "last_reviewed": (
        # NULLs first so never-reviewed skills surface; SQLite's NULLS
        # FIRST is implicit for ASC, but we spell it out for clarity.
        "CASE WHEN unaided_last_review_at IS NULL THEN 0 ELSE 1 END ASC, "
        "unaided_last_review_at ASC, canonical_name ASC"
    ),
}

# Slippage score: high = closer to falling out of unaided reach.
# The dominant term is the gap to perfect unaided retrievability;
# recent AI-assist pressure and historical lapses are secondary nudges.
# We clamp lapses and outsource at 10 each so a single pathological day
# cannot drown out the rest of the signal.
SLIPPAGE_SCORE_SQL = (
    "(1.0 - COALESCE(m.unaided_retrievability, 0.0)) * 0.6 "
    "+ MIN(COALESCE(m.unaided_lapses, 0), 10) / 10.0 * 0.2 "
    "+ MIN(COALESCE(m.outsource_count_7d, 0), 10) / 10.0 * 0.2"
)


def _fetch_skills(app: Any, *, sort: str) -> list[dict[str, Any]]:
    """Load active skills joined with mastery state, sorted by ``sort``."""
    order_by = SKILL_SORT_COLUMNS.get(sort, SKILL_SORT_COLUMNS["slippage"])
    rows = app.sqlite.conn.execute(
        f"""
        SELECT
          s.id,
          s.canonical_name,
          s.user_status,
          COALESCE(m.unaided_retrievability, 0.0) AS unaided_mastery,
          COALESCE(m.functional_stability, 0.0)   AS functional_mastery,
          m.unaided_last_review_at                AS last_reviewed_at,
          COALESCE(m.unaided_lapses, 0)           AS unaided_lapses,
          COALESCE(m.outsource_count_7d, 0)       AS outsource_count_7d,
          {SLIPPAGE_SCORE_SQL}                    AS slippage_score
        FROM skill_nodes s
        LEFT JOIN skill_mastery_state m ON m.skill_id = s.id
        WHERE s.user_status = 'active'
        ORDER BY {order_by}
        """
    ).fetchall()
    return [dict(row) for row in rows]


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
        # Today's queue preview: how many items would the selector pick
        # right now, and how long do we think that takes? We cap the
        # preview at 50 so the dashboard reflects the same upper bound
        # the /api/v1/review/start endpoint enforces.
        candidates = ItemSelector(sqlite=app.sqlite).select(limit=50)
        queue_count = len(candidates)
        estimated_minutes = queue_count * MINUTES_PER_ITEM
        return templates.TemplateResponse(
            request,
            "today.html",
            {
                "evidence_count": evidence_count,
                "skill_count": skill_count,
                "queue_count": queue_count,
                "estimated_minutes": estimated_minutes,
                "locale": "en",
            },
        )

    @api.get("/review")
    async def review(
        request: Request,
        limit: int = Query(5, ge=1, le=50),
    ):
        return templates.TemplateResponse(
            request,
            "review/index.html",
            {"limit": limit},
        )

    @api.get("/skills")
    async def skills(
        request: Request,
        sort: str = Query("slippage"),
    ):
        app = store_getter()
        skills_rows = _fetch_skills(app, sort=sort)
        sort_key = sort if sort in SKILL_SORT_COLUMNS else "slippage"
        return templates.TemplateResponse(
            request,
            "skills.html",
            {"skills": skills_rows, "sort": sort_key},
        )

    @api.get("/partials/skills-table")
    async def skills_table(
        request: Request,
        sort: str = Query("slippage"),
    ):
        """Render the table body alone (for htmx-driven sort clicks)."""
        app = store_getter()
        skills_rows = _fetch_skills(app, sort=sort)
        sort_key = sort if sort in SKILL_SORT_COLUMNS else "slippage"
        return templates.TemplateResponse(
            request,
            "partials/skills_table.html",
            {"skills": skills_rows, "sort": sort_key},
        )

    @api.get("/skills/{skill_id}")
    async def skill_detail(request: Request, skill_id: str):
        app = store_getter()
        row = app.sqlite.conn.execute(
            """
            SELECT
              s.*,
              COALESCE(m.unaided_retrievability, 0.0) AS unaided_mastery,
              COALESCE(m.functional_stability, 0.0)   AS functional_mastery,
              m.unaided_last_review_at                AS last_reviewed_at,
              COALESCE(m.unaided_reps, 0)             AS unaided_reps,
              COALESCE(m.unaided_lapses, 0)           AS unaided_lapses,
              COALESCE(m.outsource_count_7d, 0)       AS outsource_count_7d
            FROM skill_nodes s
            LEFT JOIN skill_mastery_state m ON m.skill_id = s.id
            WHERE s.id = ?
            """,
            (skill_id,),
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
