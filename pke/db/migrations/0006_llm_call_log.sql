-- up
-- Append-only audit log of every LLM call PKE made: provider, model,
-- token counts, latency, derived cost. The drift_metrics weekly job
-- reads the last 30 days to populate the llm_cost_30d ARCH-2 metric;
-- everything else can be queried for usage analysis.

CREATE TABLE IF NOT EXISTS llm_call_log (
  id                TEXT PRIMARY KEY,
  provider          TEXT NOT NULL,
  model             TEXT NOT NULL,
  call_kind         TEXT NOT NULL,
  prompt_tokens     INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd          REAL NOT NULL DEFAULT 0.0,
  latency_ms        INTEGER NOT NULL DEFAULT 0,
  error             TEXT,
  called_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_llm_call_called_at ON llm_call_log(called_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_call_provider ON llm_call_log(provider, called_at DESC);

-- down
DROP INDEX IF EXISTS idx_llm_call_provider;
DROP INDEX IF EXISTS idx_llm_call_called_at;
DROP TABLE IF EXISTS llm_call_log;
