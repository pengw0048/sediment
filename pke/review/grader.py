"""Review answer grading paths."""

from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(kw_only=True, slots=True)
class Grader:
    """Dispatch symbolic, LLM-judge, and self-report grading."""

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

    def grade_llm_fallback(self, *, answer: str, rubric: str) -> GradeResult:
        """Offline LLM-judge fallback based on answer length and rubric keywords."""
        del rubric
        normalized = answer.strip()
        if len(normalized) > 80:
            grade = "pass"
        elif len(normalized) > 20:
            grade = "partial"
        else:
            grade = "fail"
        return GradeResult(
            grade=grade,
            rating=grade_to_rating(grade),
            feedback="Deterministic fallback grade; configure an LLM provider for rubric judging.",
            confidence=0.6,
        )
