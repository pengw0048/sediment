"""llm_call_log persistence + cost computation + sink wiring."""

from __future__ import annotations

import asyncio

import pytest

from pke.extraction.llm_client import call_kind, set_call_logger
from pke.quality.llm_log import (
    PRICING,
    cost_usd_for,
    log_llm_call,
    sum_cost_usd,
)


def test_cost_usd_known_anthropic_model() -> None:
    """Haiku 4.5 prices: $1/M input, $5/M output."""
    cost = cost_usd_for("anthropic", "claude-haiku-4-5", 1_000_000, 1_000_000)
    assert abs(cost - 6.0) < 1e-9


def test_cost_usd_unknown_model_returns_zero() -> None:
    """Unknown model returns 0.0 rather than raising."""
    assert cost_usd_for("anthropic", "no-such-model", 1000, 500) == 0.0
    assert cost_usd_for("local", "qwen3", 1000, 500) == 0.0


def test_log_llm_call_persists_one_row(app) -> None:
    """log_llm_call writes the call to llm_call_log with the computed cost."""
    log_id = log_llm_call(
        app.sqlite,
        provider="anthropic",
        model="claude-haiku-4-5",
        call_kind="extract",
        prompt_tokens=1000,
        completion_tokens=2000,
        latency_ms=120,
    )
    assert log_id
    row = app.sqlite.conn.execute("SELECT * FROM llm_call_log WHERE id = ?", (log_id,)).fetchone()
    assert row is not None
    assert row["provider"] == "anthropic"
    assert row["call_kind"] == "extract"
    assert row["prompt_tokens"] == 1000
    assert row["completion_tokens"] == 2000
    # 1000/1M*1 + 2000/1M*5 = 0.001 + 0.01 = 0.011
    assert abs(float(row["cost_usd"]) - 0.011) < 1e-9


def test_sum_cost_usd_over_30_day_window(app) -> None:
    """sum_cost_usd totals cost_usd over the recent window."""
    for _ in range(5):
        log_llm_call(
            app.sqlite,
            provider="anthropic",
            model="claude-haiku-4-5",
            call_kind="extract",
            prompt_tokens=1000,
            completion_tokens=1000,  # 0.001 + 0.005 = 0.006 each
        )
    total = sum_cost_usd(app.sqlite, days=30)
    assert abs(total - 0.030) < 1e-9


def test_call_kind_context_propagates_to_sink(app) -> None:
    """The call_kind() context wrapper threads the kind through to the sink."""
    captured: list[dict[str, object]] = []

    def sink(**kw):
        captured.append(kw)

    set_call_logger(sink)
    try:
        # Simulate the AnthropicClient internals manually.
        from pke.extraction.llm_client import _emit_call_log

        with call_kind("intervention"):
            _emit_call_log(
                provider="anthropic",
                model="claude-haiku-4-5",
                prompt_tokens=100,
                completion_tokens=50,
                latency_ms=200,
                error=None,
            )
    finally:
        set_call_logger(None)

    assert len(captured) == 1
    assert captured[0]["call_kind"] == "intervention"
    assert captured[0]["provider"] == "anthropic"


def test_failed_sink_does_not_break_the_call() -> None:
    """If the sink raises, the LLM call must complete normally."""
    from pke.extraction.llm_client import _emit_call_log

    def boom(**kw):
        raise RuntimeError("sink exploded")

    set_call_logger(boom)
    try:
        _emit_call_log(
            provider="anthropic",
            model="claude-haiku-4-5",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
            error=None,
        )
    finally:
        set_call_logger(None)


def test_pricing_has_entries_for_pinned_models() -> None:
    """The PRICING table includes the models the LLM clients default to."""
    assert "claude-haiku-4-5" in PRICING["anthropic"]
    assert "gpt-5-mini" in PRICING["openai"]


@pytest.mark.asyncio
async def test_anthropic_client_logs_call_via_sink(app, monkeypatch) -> None:
    """The AnthropicClient emits a log event after a successful call."""
    from pke.extraction.llm_client import AnthropicClient

    captured: list[dict[str, object]] = []
    set_call_logger(lambda **kw: captured.append(kw))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    class _FakeUsage:
        input_tokens = 123
        output_tokens = 45

    class _FakeText:
        type = "text"
        text = '{"ok": true}'

    class _FakeResponse:
        content = [_FakeText()]
        usage = _FakeUsage()

    class _FakeMessages:
        async def create(self, **_kw):
            return _FakeResponse()

    class _FakeClient:
        def __init__(self, *_a, **_k):
            self.messages = _FakeMessages()

    monkeypatch.setattr("anthropic.AsyncAnthropic", _FakeClient)

    client = AnthropicClient()
    with call_kind("extract"):
        result = await client.complete_json(system="s", user="u")

    set_call_logger(None)
    assert result == {"ok": True}
    assert len(captured) == 1
    assert captured[0]["call_kind"] == "extract"
    assert captured[0]["prompt_tokens"] == 123
    assert captured[0]["completion_tokens"] == 45
    assert captured[0]["error"] is None


def test_app_create_wires_sink_to_sqlite(app) -> None:
    """App.create installs a sink that writes to llm_call_log."""
    from pke.extraction.llm_client import _emit_call_log

    _emit_call_log(
        provider="anthropic",
        model="claude-haiku-4-5",
        prompt_tokens=500,
        completion_tokens=100,
        latency_ms=80,
        error=None,
    )
    row = app.sqlite.conn.execute(
        "SELECT provider, model, prompt_tokens FROM llm_call_log"
    ).fetchone()
    assert row is not None
    assert row["provider"] == "anthropic"
    assert row["prompt_tokens"] == 500


def test_local_client_logs_success_via_sink(monkeypatch) -> None:
    """LocalClient.complete_json emits one llm_call_log row per successful call."""
    from pke.extraction.llm_client import LocalClient

    captured: list[dict[str, object]] = []
    set_call_logger(lambda **kw: captured.append(kw))

    class _FakeLlama:
        metadata = {
            "tokenizer.chat_template": "{% for m in messages %}{{ m.content }}{% endfor %}",
            "tokenizer.ggml.eos_token": "<|im_end|>",
            "tokenizer.ggml.bos_token": "",
        }

        def create_chat_completion(
            self,
            *,
            messages,
            temperature,
            response_format,
            chat_template_kwargs=None,
        ):
            return {
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }

    monkeypatch.setattr(LocalClient, "_llama", lambda self: _FakeLlama())
    client = LocalClient(enable_thinking=True)

    asyncio.run(client.complete_json(system="s", user="u"))
    set_call_logger(None)

    assert len(captured) == 1
    assert captured[0]["provider"] == "local"
    assert captured[0]["prompt_tokens"] == 12
    assert captured[0]["completion_tokens"] == 4
    assert captured[0]["error"] is None


def test_local_client_logs_error_via_sink(monkeypatch) -> None:
    """LocalClient failure path also emits one row with the error string."""
    from pke.extraction.llm_client import LocalClient

    captured: list[dict[str, object]] = []
    set_call_logger(lambda **kw: captured.append(kw))

    def _boom(self):
        raise RuntimeError("model file missing")

    monkeypatch.setattr(LocalClient, "_llama", _boom)
    client = LocalClient(enable_thinking=True)

    with pytest.raises(RuntimeError):
        asyncio.run(client.complete_json(system="s", user="u"))
    set_call_logger(None)
    assert len(captured) == 1
    assert captured[0]["provider"] == "local"
    assert "model file missing" in str(captured[0]["error"])


def test_failed_anthropic_call_logs_error(monkeypatch) -> None:
    """If the LLM call raises, the sink still receives a row with the error string."""
    from pke.extraction.llm_client import AnthropicClient

    captured: list[dict[str, object]] = []
    set_call_logger(lambda **kw: captured.append(kw))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    class _BoomMessages:
        async def create(self, **_kw):
            raise RuntimeError("upstream 500")

    class _BoomClient:
        def __init__(self, *_a, **_k):
            self.messages = _BoomMessages()

    monkeypatch.setattr("anthropic.AsyncAnthropic", _BoomClient)

    client = AnthropicClient()
    with pytest.raises(RuntimeError):
        asyncio.run(client.complete_json(system="s", user="u"))
    set_call_logger(None)
    assert len(captured) == 1
    assert "upstream 500" in str(captured[0]["error"])


def test_failed_openai_call_logs_error(monkeypatch) -> None:
    """OpenAI failure path also logs the error to the sink.

    Symmetric to ``test_failed_anthropic_call_logs_error``: if the
    upstream chat.completions.create coroutine raises, the
    ``_emit_call_log`` inside OpenAIClient still emits one row with
    the error string and the original exception still propagates to
    the caller.
    """
    from pke.extraction.llm_client import OpenAIClient

    captured: list[dict[str, object]] = []
    set_call_logger(lambda **kw: captured.append(kw))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class _BoomCompletions:
        async def create(self, **_kw):
            raise RuntimeError("upstream 500")

    class _BoomChat:
        def __init__(self) -> None:
            self.completions = _BoomCompletions()

    class _BoomClient:
        def __init__(self, *_a, **_k):
            self.chat = _BoomChat()

    monkeypatch.setattr("openai.AsyncOpenAI", _BoomClient)

    client = OpenAIClient()
    with pytest.raises(RuntimeError):
        asyncio.run(client.complete_json(system="s", user="u"))
    set_call_logger(None)
    assert len(captured) == 1
    assert captured[0]["provider"] == "openai"
    assert "upstream 500" in str(captured[0]["error"])


@pytest.mark.asyncio
async def test_call_kind_context_var_isolates_parallel_tasks() -> None:
    """Two ``call_kind`` blocks running under ``asyncio.gather`` keep their own kind.

    ``_CALL_KIND`` is a :class:`contextvars.ContextVar`, which means a
    write inside one Task does not leak into a sibling Task spawned
    by ``asyncio.gather``. This test pins that invariant: replace the
    ContextVar with a module-global ``str`` and this test starts to
    fail because both tasks would see whichever kind was set last.
    """
    from pke.extraction.llm_client import _emit_call_log

    captured: list[dict[str, object]] = []
    set_call_logger(lambda **kw: captured.append(kw))

    async def do_call(kind: str) -> str:
        with call_kind(kind):
            # Yield to the scheduler so the other Task gets a chance
            # to clobber the ContextVar if isolation is broken.
            await asyncio.sleep(0)
            _emit_call_log(
                provider="test",
                model="m",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                error=None,
            )
            return kind

    try:
        await asyncio.gather(do_call("extract"), do_call("judge"))
    finally:
        set_call_logger(None)

    kinds = sorted(str(rec["call_kind"]) for rec in captured)
    assert kinds == ["extract", "judge"], (
        f"ContextVar isolation broken; got {kinds!r}"
    )
