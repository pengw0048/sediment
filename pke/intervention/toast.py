"""Post-response toast payloads."""

from pke.intervention.decider import InterventionPayload


def toast_payload(payload: InterventionPayload) -> dict[str, object]:
    """Return browser/web toast payload."""
    return {
        "kind": "pke_toast",
        "skill_id": payload.skill_id,
        "question": payload.question,
        "buttons": ["Add to review", "Dismiss"],
    }
