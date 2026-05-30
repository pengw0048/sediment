"""Sandboxed code execution for symbolic graders."""

from pke.review.grader import Grader, GradeResult


def grade_python(answer: str, test_code: str) -> GradeResult:
    """Run a Python answer against test code."""
    return Grader().grade_code(answer=answer, test_code=test_code)
