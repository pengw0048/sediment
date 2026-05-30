"""LLM client wrappers for extraction, item generation, and grading."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol


class LLMClient(Protocol):
    """Async LLM client protocol."""

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        """Return strict JSON from an LLM provider."""


@dataclass(kw_only=True, slots=True)
class AnthropicClient:
    """Anthropic Messages client with prompt-caching-friendly inputs."""

    model: str = "claude-haiku-4-5"
    api_key_env: str = "ANTHROPIC_API_KEY"

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        """Call Anthropic and parse a JSON object."""
        import anthropic

        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(f"{self.api_key_env} is not set")
        client = anthropic.AsyncAnthropic(api_key=key)
        response = await client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        return dict(json.loads(text))


@dataclass(kw_only=True, slots=True)
class OpenAIClient:
    """OpenAI-compatible JSON client."""

    model: str = "gpt-5-mini"
    api_key_env: str = "OPENAI_API_KEY"

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        """Call OpenAI and parse a JSON object."""
        from openai import AsyncOpenAI

        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(f"{self.api_key_env} is not set")
        client = AsyncOpenAI(api_key=key)
        response = await client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        content = response.choices[0].message.content or "{}"
        return dict(json.loads(content))


@dataclass(kw_only=True, slots=True)
class LocalClient:
    """Offline deterministic fallback for development and no-key use."""

    enable_thinking: bool = False

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        """Return a deterministic skill extraction-like response."""
        text = user.lower()
        words = [word.strip(".,:;()[]{}") for word in text.split()]
        candidates = [word for word in words if len(word) > 4][:3] or ["general task"]
        skills = [
            {
                "name": candidate.replace("_", " "),
                "description": f"Work involving {candidate}",
                "polarity": "asked-about" if "?" in user else "demonstrated",
                "confidence": 0.65,
                "span_start": 0,
                "span_end": min(len(user), 80),
            }
            for candidate in dict.fromkeys(candidates)
        ]
        return {"skills": skills}
