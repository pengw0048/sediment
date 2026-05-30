"""Socratic decomposition generator."""

from pke.review.item_gen import GeneratedItem, ReviewItemType


def generate(skill_label: str) -> GeneratedItem:
    """Generate a Socratic decomposition item."""
    return GeneratedItem(
        item_type=ReviewItemType.SOCRATIC,
        prompt=f"What is the first concrete step for {skill_label}?",
        oracle=None,
        grader="llm_judge",
        hint_path=["Start with the smallest observable signal."],
    )
