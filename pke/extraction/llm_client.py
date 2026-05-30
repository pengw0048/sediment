"""LLM client wrappers for extraction, item generation, and grading."""

from __future__ import annotations

import json
import os
from asyncio import to_thread
from dataclasses import dataclass, field
from inspect import signature
from pathlib import Path
from typing import Any, Protocol


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
    """llama-cpp-python client for local Qwen3-8B-Instruct GGUF inference."""

    model_path: Path = field(
        default_factory=lambda: Path(
            "~/.local/share/pke/models/qwen3-8b-instruct-q4_k_m.gguf"
        ).expanduser()
    )
    context_length: int = 8192
    enable_thinking: bool = False
    n_gpu_layers: int = -1
    _model: Any | None = field(default=None, init=False, repr=False)

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        """Run Qwen locally and parse its JSON object response."""
        model = self._llama()
        kwargs: dict[str, object] = {
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        supports_template_kwargs = (
            "chat_template_kwargs" in signature(model.create_chat_completion).parameters
        )
        if supports_template_kwargs:
            kwargs["chat_template_kwargs"] = {"enable_thinking": self.enable_thinking}
        elif not self.enable_thinking:
            raise RuntimeError(
                "llama-cpp-python does not expose chat_template_kwargs; "
                "cannot enforce enable_thinking=False for Qwen3"
            )
        response = await to_thread(model.create_chat_completion, **kwargs)
        if not isinstance(response, dict):
            raise TypeError("streaming local LLM responses are not supported for JSON completion")
        content = response["choices"][0]["message"]["content"]
        return dict(json.loads(str(content)))

    def _llama(self) -> Any:
        if self._model is not None:
            return self._model
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Local Qwen3 model not found at {self.model_path}; "
                "run pke fetch-local-model before using LocalClient"
            )
        from llama_cpp import Llama

        self._model = Llama(
            model_path=str(self.model_path),
            n_ctx=self.context_length,
            n_gpu_layers=self.n_gpu_layers,
            verbose=False,
        )
        return self._model
