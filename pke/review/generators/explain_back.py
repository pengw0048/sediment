"""Explain-back item generator."""

from pke.review.item_gen import GeneratedItem, ReviewItemType


def generate(skill_label: str) -> GeneratedItem:
    """Generate an explain-back item."""
    return GeneratedItem(
        item_type=ReviewItemType.EXPLAIN_BACK,
        prompt=f"Explain {skill_label} to a friend in 30-60 seconds.",
        oracle=None,
        grader="llm_judge",
        hint_path=["Use one concrete example."],
    )
