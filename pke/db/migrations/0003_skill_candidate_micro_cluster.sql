-- up
-- Track the online micro-cluster that DenStream assigns to each candidate
-- so batch-cluster and EDC sweeps can use micro-cluster membership rather
-- than per-candidate cosine when deciding which candidates to compare.

ALTER TABLE skill_candidates ADD COLUMN micro_cluster_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_cand_micro_cluster
  ON skill_candidates(micro_cluster_id);

-- down
DROP INDEX IF EXISTS idx_cand_micro_cluster;
-- SQLite cannot drop a column before 3.35 without a table rebuild; the
-- column is left in place on downgrade. The application code tolerates
-- the column being unused.
