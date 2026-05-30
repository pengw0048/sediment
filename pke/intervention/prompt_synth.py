"""Socratic prompt synthesis."""

from pke.intervention.decider import InterventionPayload


def render_socratic_block(payload: InterventionPayload) -> str:
    """Render Claude Code additional context text."""
    return (
        "<pke-socratic>\n"
        "Before I answer, here is a 30-second self-check from PKE:\n\n"
        f"{payload.question}\n\n"
        "If you want to try it first, type your attempt; otherwise just continue "
        "your original request.\n"
        "</pke-socratic>"
    )
