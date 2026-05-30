"""Variant item generator."""

from pke.review.item_gen import GeneratedItem, ReviewItemType


def generate(skill_label: str) -> GeneratedItem:
    """Generate a variant item."""
    return GeneratedItem(
        item_type=ReviewItemType.VARIANT,
        prompt=f"Solve a nearby variant of {skill_label}.",
        oracle=None,
        grader="llm_judge",
        hint_path=["Change one parameter, not the whole task."],
    )
