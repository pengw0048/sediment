-- up
-- Anti-annoyance: persist the live intervention state and an audit log of
-- every trigger so the decider can apply consecutive-dismiss downgrades,
-- per-source overrides, daily caps, and deadline mode across restarts.

CREATE TABLE IF NOT EXISTS intervention_state (
  user_id TEXT PRIMARY KEY,
  current_strength TEXT NOT NULL CHECK(current_strength IN ('off','quiet','gentle','active')),
  consecutive_dismiss_count INTEGER NOT NULL DEFAULT 0,
  last_dismiss_at TEXT,
  last_dismiss_source TEXT,
  daily_intervention_count INTEGER NOT NULL DEFAULT 0,
  daily_count_reset_at TEXT NOT NULL DEFAULT (date('now')),
  deadline_mode_until TEXT,
  auto_downgrade_until TEXT,
  override_strengths_json TEXT
);

CREATE TABLE IF NOT EXISTS intervention_log (
  log_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  source TEXT NOT NULL,
  triggered_at TEXT NOT NULL DEFAULT (datetime('now')),
  strength_at_trigger TEXT NOT NULL,
  skill_id TEXT,
  outcome TEXT NOT NULL CHECK(outcome IN ('shown','dismissed','engaged','bypassed')),
  socratic_prompt TEXT,
  user_response TEXT,
  user_response_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_intervention_log_user_time
  ON intervention_log(user_id, triggered_at);
CREATE INDEX IF NOT EXISTS idx_intervention_log_source_outcome
  ON intervention_log(source, outcome, triggered_at);

-- down
DROP INDEX IF EXISTS idx_intervention_log_source_outcome;
DROP INDEX IF EXISTS idx_intervention_log_user_time;
DROP TABLE IF EXISTS intervention_log;
DROP TABLE IF EXISTS intervention_state;
