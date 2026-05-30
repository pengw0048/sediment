"""Pre-AI prompt injection helpers for proxy modes."""

from __future__ import annotations

from pke.intervention.decider import InterventionPayload


def openai_system_prefix(original: str, payload: InterventionPayload) -> str:
    """Append a non-binding PKE Socratic prefix to an OpenAI system message."""
    return (
        f"{original}\n\n"
        "[PKE Socratic prefix - non-binding hint to the assistant]:\n"
        "The user has not yet attempted this themselves. Before producing the full "
        "solution, optionally ask the user one question that lets them try it first. "
        "If the user clearly indicates they want the answer directly, proceed normally. "
        f'Question seed: "{payload.question}"'
    )


def anthropic_system_append(
    system_blocks: list[dict[str, str]], payload: InterventionPayload
) -> list[dict[str, str]]:
    """Append a non-binding PKE block to Anthropic system blocks."""
    return [
        *system_blocks,
        {
            "type": "text",
            "text": (
                "PKE Socratic prefix - optionally ask one self-check question before "
                f"answering directly. Question seed: {payload.question}"
            ),
        },
    ]
