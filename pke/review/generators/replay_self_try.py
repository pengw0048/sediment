"""Replay-self-try generator."""

from pke.review.item_gen import GeneratedItem, ReviewItemType


def generate(evidence_text: str) -> GeneratedItem:
    """Generate a replay-self-try item."""
    return GeneratedItem(
        item_type=ReviewItemType.REPLAY_SELF_TRY,
        prompt=f"Try this yourself without AI help:\n\n{evidence_text}",
        oracle=None,
        grader="manual",
        hint_path=[],
    )
