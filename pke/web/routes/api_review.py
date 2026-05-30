"""Review JSON/HTMX API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from pke.review.grader import Grader, GradeResult
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
        generator = ItemGenerator(client=getattr(app, "llm_client", None))
        for position, row in enumerate(rows):
            item = await generator.generate(
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
        item_id = str(payload["item_id"])
        user_answer = str(payload.get("answer", ""))
        self_rating = int(payload.get("self_rating", 2))

        # Resolve the item's grader contract before grading. The item row
        # carries ``grader`` (symbolic / llm_judge / self_report / ...) and
        # ``oracle`` (regex pattern, code, rubric JSON — grader-specific).
        item_row = app.sqlite.conn.execute(
            "SELECT prompt, grader, oracle FROM review_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if item_row is None:
            raise HTTPException(status_code=404, detail=f"review item {item_id} not found")

        grader_kind = str(item_row["grader"])
        oracle = item_row["oracle"]
        item_prompt = str(item_row["prompt"])

        grader = Grader(client=getattr(app, "llm_client", None))
        grade: GradeResult
        if grader_kind == "regex":
            grade = grader.grade_regex(answer=user_answer, pattern=str(oracle or ""))
        elif grader_kind == "code_exec":
            grade = grader.grade_code(answer=user_answer, test_code=str(oracle or ""))
        elif grader_kind == "self_report":
            grade = grader.grade_self_report(
                grade={1: "fail", 2: "partial", 3: "partial", 4: "pass"}.get(
                    self_rating, "partial"
                ),
                reason=str(payload.get("reason", "")),
            )
        elif grader_kind == "manual":
            # Manual items are reviewed offline; record a partial self-report so
            # mastery does not move on the strength of an un-judged answer.
            grade = grader.grade_self_report(grade="partial", reason="awaiting manual review")
        elif grader_kind == "llm_judge":
            rubric = Grader.parse_rubric(oracle if isinstance(oracle, str) else None)
            try:
                grade = await grader.grade_llm_judge(
                    item_prompt=item_prompt,
                    user_answer=user_answer,
                    rubric=rubric,
                )
            except RuntimeError:
                # No LLM client configured; degrade to a self-report so the
                # answer still lands but does not invent a grade.
                grade = grader.grade_self_report(
                    grade={1: "fail", 2: "partial", 3: "partial", 4: "pass"}.get(
                        self_rating, "partial"
                    ),
                    reason="no llm client; recorded as self-report",
                )
        else:
            raise HTTPException(status_code=422, detail=f"unknown grader_kind: {grader_kind!r}")

        answer_id = answer_item(
            app.sqlite,
            item_id=item_id,
            self_rating=self_rating,
            user_answer=user_answer,
            grade=grade,
            elapsed_ms=int(payload.get("elapsed_ms", 0)),
        )
        return {
            "answer_id": answer_id,
            "grade": grade.grade,
            "confidence": grade.confidence,
            "feedback": grade.feedback,
        }

    @api.post("/skip")
    async def skip(payload: dict[str, Any]) -> dict[str, Any]:
        return {"item_id": payload.get("item_id"), "status": "skipped"}

    return api
