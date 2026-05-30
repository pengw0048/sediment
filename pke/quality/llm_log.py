"""LLM call logging + cost computation.

``log_llm_call`` writes one row to ``llm_call_log`` per LLM invocation,
including provider/model, prompt and completion token counts, derived
USD cost, latency, and any error string. The LLM client wrappers in
``pke.extraction.llm_client`` call into here via a module-level sink so
the clients themselves stay independent of the storage layer; the App
container wires the sink at startup.

``sum_cost_usd`` returns the rolling cost over a window — the
``drift_metrics`` job calls it with a 30-day window for the ARCH-2
``llm_cost_30d`` metric.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pke.evidence.models import iso_utc, new_ulid

if TYPE_CHECKING:
    from pke.db.sqlite import SQLiteStore

# Per-1M-token USD rates. Update when providers change their list price;
# keep the units consistent (USD per 1,000,000 tokens, input/output split).
PRICING: dict[str, dict[str, tuple[float, float]]] = {
    "anthropic": {
        "claude-haiku-4-5": (1.0, 5.0),
        "claude-sonnet-4-6": (3.0, 15.0),
        "claude-opus-4-8": (15.0, 75.0),
    },
    "openai": {
        "gpt-5-mini": (0.25, 2.0),
        "gpt-5": (5.0, 15.0),
    },
    # Local / self-hosted endpoints have no marginal token cost; record the
    # call but log 0 so the cost metric reflects only paid providers.
    "local": {},
}


def cost_usd_for(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return derived USD cost for one call. Unknown models return 0.0."""
    provider_table = PRICING.get(provider, {})
    rates = provider_table.get(model)
    if rates is None:
        return 0.0
    input_rate, output_rate = rates
    return prompt_tokens * input_rate / 1_000_000.0 + completion_tokens * output_rate / 1_000_000.0


def log_llm_call(
    sqlite: SQLiteStore,
    *,
    provider: str,
    model: str,
    call_kind: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    error: str | None = None,
) -> str:
    """Persist one ``llm_call_log`` row. Returns the new row id."""
    cost = cost_usd_for(provider, model, prompt_tokens, completion_tokens)
    log_id = new_ulid()
    sqlite.conn.execute(
        """
        INSERT INTO llm_call_log(
          id, provider, model, call_kind, prompt_tokens, completion_tokens,
          cost_usd, latency_ms, error, called_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            log_id,
            provider,
            model,
            call_kind,
            int(prompt_tokens or 0),
            int(completion_tokens or 0),
            float(cost),
            int(latency_ms or 0),
            error,
            iso_utc(),
        ),
    )
    sqlite.conn.commit()
    return log_id


def sum_cost_usd(sqlite: SQLiteStore, *, days: int) -> float:
    """Return total ``cost_usd`` across the most recent ``days`` of calls."""
    row = sqlite.conn.execute(
        f"""
        SELECT COALESCE(SUM(cost_usd), 0.0) AS total
        FROM llm_call_log
        WHERE called_at >= datetime('now', '-{int(days)} days')
        """,
    ).fetchone()
    return float(row["total"] or 0.0)


__all__ = ["PRICING", "cost_usd_for", "log_llm_call", "sum_cost_usd"]
