-- up
-- Widen skill_candidates.resolution_state CHECK to include 'pending_audit'.
-- The gray-band judge path (identity resolver) marks ambiguous candidates
-- pending_audit and queues them for human review, but the original CHECK
-- only allowed pending/auto/manual/rejected, so that branch crashed with
-- an IntegrityError and rolled back the same-transaction pending_audits
-- write. SQLite cannot ALTER a CHECK in place, so we rebuild the table.

CREATE TABLE skill_candidates_new (
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
                    CHECK (resolution_state IN ('pending','auto','manual','rejected','pending_audit')),
  micro_cluster_id  INTEGER,
  created_at        TEXT NOT NULL
);

INSERT INTO skill_candidates_new(
  id, evidence_id, raw_name, normalized_name, description, span_start, span_end,
  evidence_kind, confidence, embedding, resolved_skill_id, resolution_state,
  micro_cluster_id, created_at
)
SELECT id, evidence_id, raw_name, normalized_name, description, span_start, span_end,
       evidence_kind, confidence, embedding, resolved_skill_id, resolution_state,
       micro_cluster_id, created_at
FROM skill_candidates;

DROP TABLE skill_candidates;
ALTER TABLE skill_candidates_new RENAME TO skill_candidates;

CREATE INDEX IF NOT EXISTS idx_cand_pending ON skill_candidates(resolution_state, created_at)
  WHERE resolution_state = 'pending';
CREATE INDEX IF NOT EXISTS idx_cand_norm ON skill_candidates(normalized_name);
CREATE INDEX IF NOT EXISTS idx_cand_micro_cluster
  ON skill_candidates(micro_cluster_id);

-- down
-- Round-tripping back to the narrower CHECK is unsafe if any rows currently
-- carry resolution_state='pending_audit'; downgrade is a no-op.
