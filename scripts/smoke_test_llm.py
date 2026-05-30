r"""Smoke-test PKE's three LLM prompts against an OpenAI-compatible endpoint.

Run with::

    PKE_LLM_BASE_URL=http://localhost:8000/v1 \
    PKE_LLM_MODEL=qwen35-397b \
        uv run python scripts/smoke_test_llm.py

Prints the JSON each prompt produced, asserts the output schema matches
what the rest of PKE expects, and exits non-zero on the first failure so
the smoke script can be used as a quick CI gate when an endpoint is
available.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from pke.extraction.llm_client import OpenAIClient
from pke.extraction.prompts import render as render_prompt
from pke.review.item_gen import ItemGenerator, ReviewItemType


def _client() -> OpenAIClient:
    base_url = os.environ.get("PKE_LLM_BASE_URL", "http://localhost:8000/v1")
    model = os.environ.get("PKE_LLM_MODEL", "qwen35-397b")
    return OpenAIClient(
        model=model,
        api_key_env=None,
        base_url=base_url,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )


async def _smoke_extract(client: OpenAIClient) -> None:
    system = render_prompt("extract_skills.system.j2")
    user = render_prompt(
        "extract_skills.user.j2",
        evidence_text=(
            "USER: My pod keeps OOMKilled. Here's my deployment:\n"
            "```yaml\nresources:\n  requests:\n    memory: 100Mi\n```\n"
            "ASSISTANT: You're hitting the kernel OOM killer because you set "
            "requests but not limits. Add limits: { memory: 512Mi }."
        ),
    )
    print("\n=== extract_skills ===")
    payload = await client.complete_json(system=system, user=user)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    _assert_extract(payload)


async def _smoke_judge(client: OpenAIClient) -> None:
    system = render_prompt("judge_answer.system.j2")
    user = render_prompt(
        "judge_answer.user.j2",
        item_prompt="For `git rebase --interactive`, what's the first concrete thing you would check?",
        user_answer="I check whether the working tree is clean with `git status` first.",
        rubric_pass="names a concrete first check such as clean working tree, current branch, or commits to rewrite",
        rubric_partial="vague answer about checking git state with no specifics",
        rubric_fail="unrelated answer, wrong tool, or empty",
    )
    print("\n=== judge_answer ===")
    payload = await client.complete_json(system=system, user=user)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    _assert_judge(payload)


async def _smoke_gray_band(client: OpenAIClient) -> None:
    system = render_prompt("identity_gray_band.system.j2")
    user = render_prompt(
        "identity_gray_band.user.j2",
        cosine=0.85,
        candidate_name="fastapi dependency injection",
        candidate_description="FastAPI's Depends() mechanism",
        existing_name="fastapi depends",
        existing_description="Declarative DI in FastAPI",
    )
    print("\n=== identity_gray_band ===")
    payload = await client.complete_json(system=system, user=user)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    _assert_gray_band(payload)


def _assert_extract(payload: dict[str, Any]) -> None:
    assert isinstance(payload, dict), payload
    assert "skills" in payload, "extract_skills payload missing 'skills'"
    assert isinstance(payload["skills"], list), "skills must be a list"
    for skill in payload["skills"]:
        assert "name" in skill, skill
        assert "polarity" in skill, skill
        assert skill["polarity"] in {"demonstrated", "attempted", "failed", "asked-about"}, skill[
            "polarity"
        ]
        assert 0.0 <= float(skill.get("confidence", 0.0)) <= 1.0, skill


def _assert_judge(payload: dict[str, Any]) -> None:
    assert payload.get("grade") in {"pass", "partial", "fail"}, payload
    assert 0.0 <= float(payload.get("confidence", 0.0)) <= 1.0, payload
    assert isinstance(payload.get("feedback", ""), str)


def _assert_gray_band(payload: dict[str, Any]) -> None:
    assert payload.get("verdict") in {"merge", "new", "pending"}, payload
    assert 0.0 <= float(payload.get("confidence", 0.0)) <= 1.0, payload


async def _smoke_item_gen(client: OpenAIClient) -> None:
    generator = ItemGenerator(client=client)
    skill = "kubernetes resource requests vs limits"
    evidence = (
        "USER: My pod keeps OOMKilled.\n" "ASSISTANT fixed it by adding limits: { memory: 512Mi }."
    )
    for item_type in (
        ReviewItemType.REPLAY_SELF_TRY,
        ReviewItemType.SOCRATIC,
        ReviewItemType.VARIANT,
        ReviewItemType.EXPLAIN_BACK,
    ):
        print(f"\n=== item_gen: {item_type.value} ===")
        item = await generator.generate(
            skill_label=skill,
            evidence_text=evidence,
            unaided_mastery=0.3,
            evidence_count=5,
            item_type=item_type,
            recent_outsource_count=3,
        )
        print(
            json.dumps(
                {
                    "prompt": item.prompt,
                    "grader": item.grader,
                    "oracle": item.oracle,
                    "hint_path": item.hint_path,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        assert item.prompt, item
        assert item.grader in {"llm_judge", "regex", "code_exec", "self_report", "manual"}, item
        assert item.hint_path, item


async def main() -> None:
    client = _client()
    failures: list[str] = []
    for name, runner in [
        ("extract", _smoke_extract),
        ("judge", _smoke_judge),
        ("gray_band", _smoke_gray_band),
        ("item_gen", _smoke_item_gen),
    ]:
        try:
            await runner(client)
        except Exception as exc:
            failures.append(f"{name}: {exc}")
            print(f"FAIL {name}: {exc}", file=sys.stderr)
    print()
    if failures:
        print(f"smoke test: {len(failures)} failures")
        sys.exit(1)
    print("smoke test: all prompts produced schema-conformant JSON")


if __name__ == "__main__":
    asyncio.run(main())
