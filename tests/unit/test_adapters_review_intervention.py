"""Adapters, review, and intervention tests."""

import json

from pke.adapters.browser_ext_endpoint import event_from_browser_payload
from pke.adapters.chatgpt_history import import_conversations_json
from pke.adapters.claude_code_hook import event_from_hook_envelope, install_settings_hook
from pke.adapters.cursor import parse_agent_transcript
from pke.intervention.decider import InterventionDecider
from pke.intervention.strength import StrengthLevel
from pke.review.grader import Grader
from pke.review.item_gen import ItemGenerator, ReviewItemType


def test_claude_code_hook_installer_merges(tmp_path):
    settings = tmp_path / "settings.json"
    install_settings_hook(settings)
    data = json.loads(settings.read_text())
    assert "UserPromptSubmit" in data["hooks"]
    assert "PostToolUse" in data["hooks"]


def test_hook_envelope_maps_to_event():
    event = event_from_hook_envelope(
        {"kind": "user_prompt", "session_id": "s1", "received_at": 1.0, "raw": {"prompt": "hello"}}
    )
    assert event.source == "claude_code_hook"
    assert event.conversation_id == "cc_s1"


def test_cursor_jsonl_parser(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text('{"type":"user_message","payload":{"text":"hello"}}\n', encoding="utf-8")
    assert parse_agent_transcript(path)[0].source == "cursor_tail"


def test_chatgpt_importer(tmp_path):
    path = tmp_path / "conversations.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "c1",
                    "create_time": 1.0,
                    "current_node": "a2",
                    "mapping": {
                        "u1": {
                            "message": {
                                "id": "u1",
                                "author": {"role": "user"},
                                "create_time": 1.0,
                                "content": {"parts": ["question"]},
                            },
                            "parent": None,
                        },
                        "a2": {
                            "message": {
                                "id": "a2",
                                "author": {"role": "assistant"},
                                "content": {"parts": ["answer"]},
                            },
                            "parent": "u1",
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    events = import_conversations_json(path)
    assert len(events) == 1
    assert events[0].app == "chatgpt_web"


def test_browser_payload_maps_to_event():
    event = event_from_browser_payload(
        {"url": "https://chatgpt.com/backend-api/conversation", "reqBody": "q", "body": "a"}
    )
    assert event.source == "browser_ext"
    assert event.app == "chatgpt_web"


async def test_review_item_generation_and_grading():
    item = await ItemGenerator().generate(
        skill_label="async context managers",
        evidence_text="why __aenter__",
        unaided_mastery=0.1,
        evidence_count=3,
    )
    assert item.item_type == ReviewItemType.SOCRATIC
    assert Grader().grade_regex(answer="__aenter__", pattern="aenter").grade == "pass"


def test_intervention_levels_and_dismiss_downgrade():
    decider = InterventionDecider(per_source={"browser_ext": StrengthLevel.ACTIVE})
    payload = decider.should_intervene(
        source="browser_ext",
        skill_id="s1",
        skill_label="FastAPI routes",
        unaided_mastery=0.5,
    )
    assert payload is not None
    for _ in range(5):
        decider.record_outcome("dismissed_immediately")
    assert decider.per_source["browser_ext"] == StrengthLevel.GENTLE


async def test_item_generator_with_llm_parses_full_payload():
    """ItemGenerator uses the LLM client when configured and parses its JSON."""

    class _FakeLLM:
        async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
            del system, user
            return {
                "prompt_to_user": "Walk through 56 x 47.",
                "grader_kind": "llm_judge",
                "oracle": {"pass": "2632", "partial": "off by one digit", "fail": "wrong product"},
                "hint_path": ["Start with 56 * 7.", "Then 56 * 40.", "Add them."],
                "estimated_minutes": 3,
            }

    item = await ItemGenerator(client=_FakeLLM()).generate(
        skill_label="two-digit multiplication",
        evidence_text="47 x 38",
        unaided_mastery=0.5,
        evidence_count=4,
        item_type=ReviewItemType.VARIANT,
    )
    assert item.prompt == "Walk through 56 x 47."
    assert item.grader == "llm_judge"
    assert item.oracle is not None
    assert "2632" in item.oracle
    assert len(item.hint_path) == 3


async def test_item_generator_calibration_is_template_only():
    """Calibration-only items skip the LLM entirely."""

    class _ForbidLLM:
        async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
            raise AssertionError("calibration_only must not call the LLM")

    item = await ItemGenerator(client=_ForbidLLM()).generate(
        skill_label="kubectl rollout restart",
        evidence_text="",
        unaided_mastery=0.0,
        evidence_count=0,
    )
    assert item.item_type == ReviewItemType.CALIBRATION_ONLY
    assert item.grader == "self_report"
