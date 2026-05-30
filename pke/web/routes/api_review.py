"""Review JSON/HTMX API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pke.review.grader import Grader
from pke.review.item_gen import ItemGenerator
from pke.review.session import add_item, answer_item, create_session


def router(store_getter: Any) -> APIRouter:
    """Build review API routes."""
    api = APIRouter(prefix="/api/v1/review")

    @api.post("/start")
    async def start(payload: dict[str, Any]) -> dict[str, Any]:
        app = store_getter()
        limit = int(payload.get("limit", 5))
        session = create_session(
            app.sqlite, client=str(payload.get("client", "web")), selected_count=limit
        )
        rows = app.sqlite.conn.execute(
            "SELECT id, canonical_name FROM skill_nodes WHERE user_status='active' LIMIT ?",
            (limit,),
        ).fetchall()
        item_ids: list[str] = []
        generator = ItemGenerator()
        for position, row in enumerate(rows):
            item = generator.generate(
                skill_label=str(row["canonical_name"]),
                evidence_text="",
                unaided_mastery=0.0,
                evidence_count=1,
            )
            item_ids.append(
                add_item(
                    app.sqlite,
                    session_id=session.id,
                    skill_id=str(row["id"]),
                    item=item,
                    position=position,
                )
            )
        return {"session_id": session.id, "items": item_ids}

    @api.post("/answer")
    async def answer(payload: dict[str, Any]) -> dict[str, Any]:
        app = store_getter()
        grade = Grader().grade_llm_fallback(
            answer=str(payload.get("answer", "")),
            rubric=str(payload.get("rubric", "")),
        )
        answer_id = answer_item(
            app.sqlite,
            item_id=str(payload["item_id"]),
            self_rating=int(payload.get("self_rating", 2)),
            user_answer=str(payload.get("answer", "")),
            grade=grade,
            elapsed_ms=int(payload.get("elapsed_ms", 0)),
        )
        return {"answer_id": answer_id, "grade": grade.grade}

    @api.post("/skip")
    async def skip(payload: dict[str, Any]) -> dict[str, Any]:
        return {"item_id": payload.get("item_id"), "status": "skipped"}

    return api
