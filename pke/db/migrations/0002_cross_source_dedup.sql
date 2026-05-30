-- up
-- B14: cross-source dedup stage 2.
--
-- When two adapters (e.g. Claude Code hook + tailer + history importer)
-- observe the same logical turn, EvidenceStore.add() must recognize that
-- it is the same event and record the second observer rather than insert
-- a duplicate. The lookup is by (content_hash, role, occurred_at within a
-- small window); this index speeds it up. The window check itself lives
-- in Python because SQLite has no native interval index.
--
-- The original evidence_events row stays untouched (preserves append-only
-- semantics). Each cross-source observation is recorded in a separate
-- evidence_observers table.

CREATE INDEX IF NOT EXISTS idx_evidence_cross_source
  ON evidence_events(content_hash, role, occurred_at);

CREATE TABLE IF NOT EXISTS evidence_observers (
  evidence_id        TEXT NOT NULL REFERENCES evidence_events(id) ON DELETE CASCADE,
  source             TEXT NOT NULL,
  source_session_id  TEXT,
  observed_at        TEXT NOT NULL,
  tags_json          TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (evidence_id, source)
);
CREATE INDEX IF NOT EXISTS idx_evidence_observers_source
  ON evidence_observers(source, observed_at);

-- down
DROP INDEX IF EXISTS idx_evidence_observers_source;
DROP TABLE IF EXISTS evidence_observers;
DROP INDEX IF EXISTS idx_evidence_cross_source;
