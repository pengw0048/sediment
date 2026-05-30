-- up
BEGIN;

CREATE TABLE IF NOT EXISTS evidence_events (
  id                TEXT PRIMARY KEY,
  source            TEXT NOT NULL,
  source_session_id TEXT NOT NULL,
  role              TEXT NOT NULL CHECK (role IN ('user','assistant','tool_call','tool_result','system')),
  content           TEXT NOT NULL,
  content_hash      TEXT NOT NULL,
  tool_name         TEXT,
  metadata_json     TEXT NOT NULL DEFAULT '{}',
  occurred_at       TEXT NOT NULL,
  ingested_at       TEXT NOT NULL,
  extraction_state  TEXT NOT NULL DEFAULT 'pending'
                    CHECK (extraction_state IN ('pending','running','done','skipped','error')),
  extraction_error  TEXT
);
CREATE INDEX IF NOT EXISTS idx_evidence_pending ON evidence_events(extraction_state, occurred_at)
  WHERE extraction_state = 'pending';
CREATE INDEX IF NOT EXISTS idx_evidence_session ON evidence_events(source_session_id, occurred_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_hash ON evidence_events(source, source_session_id, content_hash);

CREATE TRIGGER IF NOT EXISTS trg_evidence_no_update
BEFORE UPDATE ON evidence_events
WHEN OLD.id != NEW.id
  OR OLD.source != NEW.source
  OR OLD.source_session_id != NEW.source_session_id
  OR OLD.role != NEW.role
  OR OLD.content != NEW.content
  OR OLD.content_hash != NEW.content_hash
  OR COALESCE(OLD.tool_name, '') != COALESCE(NEW.tool_name, '')
  OR OLD.metadata_json != NEW.metadata_json
  OR OLD.occurred_at != NEW.occurred_at
  OR OLD.ingested_at != NEW.ingested_at
BEGIN
  SELECT RAISE(ABORT, 'evidence_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_evidence_no_delete
BEFORE DELETE ON evidence_events
BEGIN
  SELECT RAISE(ABORT, 'evidence_events is append-only');
END;

CREATE TABLE IF NOT EXISTS skill_candidates (
  id                TEXT PRIMARY KEY,
  evidence_id       TEXT NOT NULL REFERENCES evidence_events(id) ON DELETE CASCADE,
  raw_name          TEXT NOT NULL,
  normalized_name   TEXT NOT NULL,
  description       TEXT,
  span_start        INTEGER,
  span_end          INTEGER,
  evidence_kind     TEXT NOT NULL
                    CHECK (evidence_kind IN ('asked','executed','failed','demonstrated')),
  confidence        REAL NOT NULL,
  embedding         BLOB,
  resolved_skill_id TEXT,
  resolution_state  TEXT NOT NULL DEFAULT 'pending'
                    CHECK (resolution_state IN ('pending','auto','manual','rejected')),
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cand_pending ON skill_candidates(resolution_state, created_at)
  WHERE resolution_state = 'pending';
CREATE INDEX IF NOT EXISTS idx_cand_norm ON skill_candidates(normalized_name);
CREATE INDEX IF NOT EXISTS idx_cand_skill ON skill_candidates(resolved_skill_id);

CREATE TABLE IF NOT EXISTS skill_nodes (
  id                TEXT PRIMARY KEY,
  canonical_name    TEXT NOT NULL UNIQUE,
  description       TEXT,
  embedding         BLOB NOT NULL,
  cluster_size      INTEGER NOT NULL DEFAULT 1,
  first_seen_at     TEXT NOT NULL,
  last_seen_at      TEXT NOT NULL,
  user_status       TEXT NOT NULL DEFAULT 'active'
                    CHECK (user_status IN ('active','dropped','already_known')),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skill_status ON skill_nodes(user_status);
CREATE INDEX IF NOT EXISTS idx_skill_lastseen ON skill_nodes(last_seen_at);

CREATE TABLE IF NOT EXISTS skill_aliases (
  id          TEXT PRIMARY KEY,
  skill_id    TEXT NOT NULL REFERENCES skill_nodes(id) ON DELETE CASCADE,
  alias       TEXT NOT NULL,
  source      TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  UNIQUE(skill_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_alias_text ON skill_aliases(alias);

CREATE TABLE IF NOT EXISTS skill_evidence_link (
  skill_id      TEXT NOT NULL REFERENCES skill_nodes(id) ON DELETE CASCADE,
  evidence_id   TEXT NOT NULL REFERENCES evidence_events(id) ON DELETE CASCADE,
  candidate_id  TEXT NOT NULL REFERENCES skill_candidates(id) ON DELETE CASCADE,
  evidence_kind TEXT NOT NULL,
  occurred_at   TEXT NOT NULL,
  PRIMARY KEY (skill_id, evidence_id, candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_sel_skill_time ON skill_evidence_link(skill_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS skill_mastery_state (
  skill_id               TEXT PRIMARY KEY REFERENCES skill_nodes(id) ON DELETE CASCADE,
  unaided_stability      REAL NOT NULL DEFAULT 0,
  unaided_difficulty     REAL NOT NULL DEFAULT 5.0,
  unaided_retrievability REAL NOT NULL DEFAULT 0,
  unaided_halflife_h     REAL NOT NULL DEFAULT 24.0,
  unaided_state          TEXT NOT NULL DEFAULT 'new'
                         CHECK (unaided_state IN ('new','learning','review','relearning')),
  unaided_reps           INTEGER NOT NULL DEFAULT 0,
  unaided_lapses         INTEGER NOT NULL DEFAULT 0,
  unaided_last_review_at TEXT,
  unaided_due_at         TEXT,
  functional_stability   REAL NOT NULL DEFAULT 0,
  functional_difficulty  REAL NOT NULL DEFAULT 5.0,
  functional_halflife_h  REAL NOT NULL DEFAULT 24.0,
  functional_state       TEXT NOT NULL DEFAULT 'new',
  functional_reps        INTEGER NOT NULL DEFAULT 0,
  functional_last_at     TEXT,
  outsource_count_7d     INTEGER NOT NULL DEFAULT 0,
  outsource_count_30d    INTEGER NOT NULL DEFAULT 0,
  updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mastery_due ON skill_mastery_state(unaided_due_at);

CREATE TABLE IF NOT EXISTS review_sessions (
  id              TEXT PRIMARY KEY,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  client          TEXT NOT NULL CHECK (client IN ('web','tui','cli')),
  selected_count  INTEGER NOT NULL,
  completed_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS review_items (
  id                 TEXT PRIMARY KEY,
  session_id          TEXT NOT NULL REFERENCES review_sessions(id) ON DELETE CASCADE,
  skill_id            TEXT NOT NULL REFERENCES skill_nodes(id),
  item_type           TEXT NOT NULL CHECK (item_type IN ('replay_self_try','socratic','variant','explain_back','calibration_only')),
  prompt              TEXT NOT NULL,
  oracle              TEXT,
  grader              TEXT NOT NULL CHECK (grader IN ('llm_judge','code_exec','regex','manual','self_report')),
  origin_evidence_id  TEXT REFERENCES evidence_events(id),
  presented_at        TEXT NOT NULL,
  position            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_session ON review_items(session_id, position);

CREATE TABLE IF NOT EXISTS review_answers (
  id              TEXT PRIMARY KEY,
  item_id         TEXT NOT NULL UNIQUE REFERENCES review_items(id) ON DELETE CASCADE,
  self_rating     INTEGER NOT NULL CHECK (self_rating BETWEEN 1 AND 4),
  user_answer     TEXT NOT NULL,
  grade           INTEGER NOT NULL CHECK (grade BETWEEN 1 AND 4),
  judge_reasoning TEXT,
  answered_at     TEXT NOT NULL,
  elapsed_ms      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration_log (
  id           TEXT PRIMARY KEY,
  skill_id     TEXT NOT NULL REFERENCES skill_nodes(id),
  answer_id    TEXT NOT NULL REFERENCES review_answers(id),
  predicted    REAL NOT NULL,
  actual       REAL NOT NULL,
  brier        REAL NOT NULL,
  occurred_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calib_skill ON calibration_log(skill_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS interventions (
  id            TEXT PRIMARY KEY,
  source        TEXT NOT NULL,
  skill_id      TEXT REFERENCES skill_nodes(id),
  strength      TEXT NOT NULL CHECK (strength IN ('quiet','gentle','active')),
  mode          TEXT NOT NULL CHECK (mode IN ('pre_ai','post_response_toast')),
  prompt_text   TEXT NOT NULL,
  evidence_id   TEXT REFERENCES evidence_events(id),
  fired_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interv_skill ON interventions(skill_id, fired_at DESC);

CREATE TABLE IF NOT EXISTS intervention_outcomes (
  intervention_id TEXT PRIMARY KEY REFERENCES interventions(id) ON DELETE CASCADE,
  outcome         TEXT NOT NULL CHECK (outcome IN ('dismissed','accepted','tried','tried_then_asked_ai')),
  user_note       TEXT,
  recorded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS adapter_state (
  adapter_name TEXT PRIMARY KEY,
  cursor_json  TEXT NOT NULL,
  last_run_at  TEXT NOT NULL,
  last_status  TEXT NOT NULL CHECK (last_status IN ('ok','error')),
  last_error   TEXT
);

CREATE TABLE IF NOT EXISTS pending_audits (
  id            TEXT PRIMARY KEY,
  audit_type    TEXT NOT NULL CHECK (audit_type IN ('split','merge','candidate_review')),
  payload_json  TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  resolved_at   TEXT,
  resolution    TEXT CHECK (resolution IN ('confirmed','rejected','deferred'))
);
CREATE INDEX IF NOT EXISTS idx_audit_open ON pending_audits(audit_type, created_at)
  WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS quality_metrics (
  id           TEXT PRIMARY KEY,
  metric_name  TEXT NOT NULL,
  value        REAL NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  recorded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quality_metric ON quality_metrics(metric_name, recorded_at DESC);

INSERT OR IGNORE INTO schema_version(version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ','now'));

COMMIT;

-- down
BEGIN;
DROP TABLE IF EXISTS quality_metrics;
DROP TABLE IF EXISTS pending_audits;
DROP TABLE IF EXISTS adapter_state;
DROP TABLE IF EXISTS settings;
DROP TABLE IF EXISTS intervention_outcomes;
DROP TABLE IF EXISTS interventions;
DROP TABLE IF EXISTS calibration_log;
DROP TABLE IF EXISTS review_answers;
DROP TABLE IF EXISTS review_items;
DROP TABLE IF EXISTS review_sessions;
DROP TABLE IF EXISTS skill_mastery_state;
DROP TABLE IF EXISTS skill_evidence_link;
DROP TABLE IF EXISTS skill_aliases;
DROP TABLE IF EXISTS skill_nodes;
DROP TABLE IF EXISTS skill_candidates;
DROP TRIGGER IF EXISTS trg_evidence_no_delete;
DROP TRIGGER IF EXISTS trg_evidence_no_update;
DROP TABLE IF EXISTS evidence_events;
DELETE FROM schema_version WHERE version = 1;
COMMIT;
