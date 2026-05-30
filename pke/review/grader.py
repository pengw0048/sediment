"""Review answer grading paths."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pke.extraction.llm_client import LLMClient
from pke.extraction.prompts import render as render_prompt


@dataclass(frozen=True, kw_only=True, slots=True)
class GradeResult:
    """Normalized grade result."""

    grade: str
    rating: int
    feedback: str
    confidence: float


def grade_to_rating(grade: str) -> int:
    """Map pass/partial/fail to FSRS 1-4 rating."""
    return {"pass": 4, "partial": 2, "fail": 1}.get(grade, 2)


# Items with judge confidence below this threshold are surfaced as low-confidence
# so we never apply a heavily-uncertain LLM grade to long-lived mastery state.
LLM_JUDGE_MIN_CONFIDENCE = 0.6


@dataclass(kw_only=True, slots=True)
class Grader:
    """Dispatch symbolic, LLM-judge, and self-report grading."""

    client: LLMClient | None = field(default=None)

    def grade_regex(self, *, answer: str, pattern: str) -> GradeResult:
        """Grade with a regex oracle."""
        matched = re.search(pattern, answer, flags=re.IGNORECASE | re.MULTILINE) is not None
        grade = "pass" if matched else "fail"
        return GradeResult(
            grade=grade, rating=grade_to_rating(grade), feedback=grade, confidence=1.0
        )

    def grade_shell_args(self, *, answer: str, expected_argv: list[str]) -> GradeResult:
        """Grade shell command answers by argv set."""
        try:
            argv = shlex.split(answer)
        except ValueError:
            return GradeResult(
                grade="fail", rating=1, feedback="Could not parse command.", confidence=1.0
            )
        grade = (
            "pass"
            if set(argv) == set(expected_argv)
            else "partial"
            if set(expected_argv) & set(argv)
            else "fail"
        )
        return GradeResult(
            grade=grade, rating=grade_to_rating(grade), feedback=grade, confidence=1.0
        )

    def grade_code(self, *, answer: str, test_code: str, timeout_s: int = 5) -> GradeResult:
        """Run a small Python symbolic grader in a temporary directory."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answer.py"
            path.write_text(answer + "\n\n" + test_code, encoding="utf-8")
            proc = subprocess.run(
                ["python3", str(path)],
                cwd=tmp,
                text=True,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        grade = "pass" if proc.returncode == 0 else "fail"
        feedback = proc.stdout.strip() or proc.stderr.strip() or grade
        return GradeResult(
            grade=grade, rating=grade_to_rating(grade), feedback=feedback, confidence=1.0
        )

    def grade_self_report(self, *, grade: str, reason: str = "") -> GradeResult:
        """Trust self-report with lower confidence."""
        return GradeResult(
            grade=grade, rating=grade_to_rating(grade), feedback=reason or grade, confidence=0.5
        )

    async def grade_llm_judge(
        self,
        *,
        item_prompt: str,
        user_answer: str,
        rubric: dict[str, str],
    ) -> GradeResult:
        """Grade a free-text answer with an LLM judge constrained by a rubric.

        ``rubric`` must contain ``pass``, ``partial``, ``fail`` keys describing
        what each grade looks like for the specific item. If the judge returns
        a confidence below :data:`LLM_JUDGE_MIN_CONFIDENCE`, we surface the
        result as low-confidence so downstream code can fall back to self-report.
        """
        if self.client is None:
            raise RuntimeError(
                "Grader requires an LLMClient for grade_llm_judge; "
                "construct Grader(client=...) with an Anthropic / OpenAI / Local client."
            )
        system = render_prompt("judge_answer.system.j2")
        user = render_prompt(
            "judge_answer.user.j2",
            item_prompt=item_prompt,
            user_answer=user_answer,
            rubric_pass=rubric.get("pass", ""),
            rubric_partial=rubric.get("partial", ""),
            rubric_fail=rubric.get("fail", ""),
        )
        payload = await self.client.complete_json(system=system, user=user)
        grade = str(payload.get("grade", "fail")).lower()
        if grade not in {"pass", "partial", "fail"}:
            grade = "fail"
        raw_confidence = payload.get("confidence", 0.0)
        try:
            confidence = float(raw_confidence)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            confidence = 0.0
        feedback = str(payload.get("feedback", "")).strip() or grade
        if confidence < LLM_JUDGE_MIN_CONFIDENCE:
            return GradeResult(
                grade=grade,
                rating=grade_to_rating(grade),
                feedback=f"[low-confidence judge] {feedback}",
                confidence=confidence,
            )
        return GradeResult(
            grade=grade,
            rating=grade_to_rating(grade),
            feedback=feedback,
            confidence=confidence,
        )

    @staticmethod
    def parse_rubric(oracle: str | None) -> dict[str, str]:
        """Best-effort parse of the ``review_items.oracle`` column into a rubric.

        The oracle column may hold a JSON object ``{"pass": "...", ...}`` or a
        plain string. Plain strings are treated as the ``pass`` rubric only.
        """
        if not oracle:
            return {"pass": "", "partial": "", "fail": ""}
        try:
            parsed = json.loads(oracle)
        except json.JSONDecodeError:
            return {"pass": oracle, "partial": "", "fail": ""}
        if not isinstance(parsed, dict):
            return {"pass": str(parsed), "partial": "", "fail": ""}
        return {
            "pass": str(parsed.get("pass", "")),
            "partial": str(parsed.get("partial", "")),
            "fail": str(parsed.get("fail", "")),
        }
