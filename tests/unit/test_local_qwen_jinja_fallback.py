"""LocalClient Jinja2 fallback for llama-cpp-python builds without chat_template_kwargs."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pke.extraction.llm_client import LocalClient


class _OlderLlama:
    """Mimic a llama-cpp-python build that lacks chat_template_kwargs."""

    def __init__(self) -> None:
        self.metadata = {
            "tokenizer.chat_template": (
                "{% for m in messages %}<|im_start|>{{ m['role'] }}\n"
                "{{ m['content'] }}<|im_end|>\n{% endfor %}"
                "{% if add_generation_prompt %}<|im_start|>assistant\n"
                "{% if enable_thinking %}<|think|>{% endif %}{% endif %}"
            ),
            "tokenizer.ggml.eos_token": "<|im_end|>",
            "tokenizer.ggml.bos_token": "",
        }
        self.captured_prompt: str | None = None

    def create_chat_completion(self, *, messages: list[dict[str, str]]) -> dict[str, Any]:
        raise AssertionError("LocalClient must NOT call create_chat_completion on older builds")

    def create_completion(self, *, prompt: str, **_: Any) -> dict[str, Any]:
        self.captured_prompt = prompt
        return {"choices": [{"text": json.dumps({"ok": True})}]}


class _NewerLlama:
    """Mimic a llama-cpp-python build that accepts chat_template_kwargs."""

    def __init__(self) -> None:
        self.captured_kwargs: dict[str, Any] | None = None

    def create_chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float,
        response_format: dict[str, str],
        chat_template_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.captured_kwargs = {
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
            "chat_template_kwargs": chat_template_kwargs,
        }
        return {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}


def test_local_client_falls_back_to_jinja2_when_llama_lacks_chat_template_kwargs() -> None:
    """Older llama-cpp-python builds drop chat_template_kwargs; render the template ourselves."""
    fake = _OlderLlama()
    client = LocalClient(enable_thinking=False)
    object.__setattr__(client, "_model", fake)

    result = asyncio.run(client.complete_json(system="sys", user="usr"))

    assert result == {"ok": True}
    assert fake.captured_prompt is not None
    # The custom Jinja branch must run and must NOT have flipped on thinking.
    assert "<|im_start|>system\nsys<|im_end|>" in fake.captured_prompt
    assert "<|im_start|>user\nusr<|im_end|>" in fake.captured_prompt
    assert "<|think|>" not in fake.captured_prompt


def test_local_client_uses_chat_template_kwargs_when_llama_supports_them() -> None:
    """Newer llama-cpp-python builds: pass enable_thinking through directly."""
    fake = _NewerLlama()
    client = LocalClient(enable_thinking=False)
    object.__setattr__(client, "_model", fake)

    asyncio.run(client.complete_json(system="sys", user="usr"))

    assert fake.captured_kwargs is not None
    assert fake.captured_kwargs["chat_template_kwargs"] == {"enable_thinking": False}
