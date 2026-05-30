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
    """OpenAI-compatible JSON client.

    Works against the official OpenAI API and against any server that
    exposes the same wire protocol (vLLM, sglang, OpenRouter, local
    Ollama proxy, the user's own tunnelled Qwen, ...). Override
    ``base_url`` to point at a non-OpenAI endpoint; set ``api_key_env``
    to a variable name that contains your key, or to ``None`` if the
    endpoint requires no auth (common for tunneled local servers).

    ``extra_body`` is forwarded to the underlying request body and is the
    standard way to pass server-specific knobs such as
    ``chat_template_kwargs={"enable_thinking": False}`` for Qwen3 family
    models served by vLLM. Default is ``None``.
    """

    model: str = "gpt-5-mini"
    api_key_env: str | None = "OPENAI_API_KEY"
    base_url: str | None = None
    extra_body: dict[str, object] | None = None

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        """Call OpenAI (or an OpenAI-compatible server) and parse a JSON object."""
        from openai import AsyncOpenAI

        if self.api_key_env is None:
            # Local or otherwise auth-less endpoint. OpenAI SDK still wants a
            # non-empty string, so pass a placeholder; the server ignores it.
            key: str | None = "no-key-required"
        else:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise RuntimeError(f"{self.api_key_env} is not set")
        client = AsyncOpenAI(api_key=key, base_url=self.base_url)
        request_kwargs: dict[str, object] = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.extra_body is not None:
            request_kwargs["extra_body"] = self.extra_body
        response = await client.chat.completions.create(**request_kwargs)  # type: ignore[call-overload]
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
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        supports_template_kwargs = (
            "chat_template_kwargs" in signature(model.create_chat_completion).parameters
        )
        if supports_template_kwargs:
            response = await to_thread(
                model.create_chat_completion,
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
                chat_template_kwargs={"enable_thinking": self.enable_thinking},
            )
            if not isinstance(response, dict):
                raise TypeError("streaming local LLM responses are not supported")
            content = response["choices"][0]["message"]["content"]
            return dict(json.loads(str(content)))

        rendered = self._render_with_jinja2(model, messages)
        response = await to_thread(
            model.create_completion,
            prompt=rendered,
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=self.context_length // 2,
            stop=["<|im_end|>", "<|endoftext|>"],
        )
        if not isinstance(response, dict):
            raise TypeError("streaming local LLM responses are not supported")
        content = response["choices"][0]["text"]
        return dict(json.loads(str(content)))

    def _render_with_jinja2(self, model: Any, messages: list[dict[str, str]]) -> str:
        """Render the chat template by hand so ``enable_thinking`` reaches Jinja.

        Older llama-cpp-python builds drop ``chat_template_kwargs`` on the floor
        in ``create_chat_completion``; on those, Qwen3 emits its ``<think>``
        preamble before the JSON body and the parser fails. Pulling the
        template out of GGUF metadata and feeding it to
        ``Jinja2ChatFormatter(..., **kwargs)`` ourselves bypasses the upstream
        gap.
        """
        from llama_cpp.llama_chat_format import Jinja2ChatFormatter

        metadata = getattr(model, "metadata", {}) or {}
        template = metadata.get("tokenizer.chat_template")
        if not template:
            sidecar = self.model_path.with_name("qwen3-tokenizer_config.json")
            if sidecar.exists():
                template = json.loads(sidecar.read_text()).get("chat_template")
        if not template:
            raise RuntimeError(
                "Qwen3 chat template is not in GGUF metadata and no sidecar "
                f"tokenizer_config.json was found next to {self.model_path}. "
                "Re-fetch the model with `pke fetch-local-model` or upgrade "
                "llama-cpp-python to a build that accepts chat_template_kwargs."
            )
        formatter = Jinja2ChatFormatter(
            template=str(template),
            eos_token=str(metadata.get("tokenizer.ggml.eos_token") or "<|im_end|>"),
            bos_token=str(metadata.get("tokenizer.ggml.bos_token") or ""),
            add_generation_prompt=True,
        )
        formatted = formatter(
            messages=messages,  # type: ignore[arg-type]
            enable_thinking=self.enable_thinking,
        )
        return str(formatted.prompt)

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


# Cross-provider fallback (try Anthropic, then OpenAI-compat, then local)
# is delegated to LiteLLM at the App layer; this module exposes the three
# single-provider clients as building blocks.
