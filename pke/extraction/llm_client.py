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
        """Call Anthropic and parse a JSON object.

        The ``system`` block is sent as a single text block with ``cache_control``
        set to ``ephemeral`` so Anthropic's prompt cache stays warm across calls
        that share the same extraction / intervention / grading prompt prefix.
        """
        import anthropic

        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(f"{self.api_key_env} is not set")
        client = anthropic.AsyncAnthropic(api_key=key)
        # ``cache_control`` is an Anthropic prompt-caching marker. It is not
        # declared in the SDK's TypedDict for system blocks (still beta in the
        # type stubs) but is part of the Messages API contract.
        system_blocks: list[dict[str, object]] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        response = await client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_blocks,  # type: ignore[arg-type]
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
            raise NotImplementedError(
                "Local Qwen3 with enable_thinking=False is not yet supported on "
                "llama-cpp-python (the installed version does not accept "
                "chat_template_kwargs in create_chat_completion). Two options:\n"
                "  (1) set enable_thinking=True in settings if you can tolerate "
                "      Qwen3 thinking tokens leaking into JSON output, or\n"
                "  (2) wait for the Jinja2ChatFormatter + create_completion "
                "      fallback path (tracked as BLOCKER.md B15b, planned PR-3+).\n"
                "Until then, prefer the Anthropic Haiku 4.5 default."
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
