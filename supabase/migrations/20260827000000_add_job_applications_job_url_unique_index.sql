-- job_applications.job_url dedup backstop.
--
-- Sub-project: full-fledged buildout, Phase 2 (job & company discovery). job_discovery.py scans
-- the same companies repeatedly (every manual run), so without this a repeated scan would create
-- duplicate 'saved' rows for postings already seen. db.create_job_application does its own
-- select-before-insert check (application-level dedup); this partial unique index is the
-- database-level backstop against a race between two concurrent inserts for the same job_url.
-- Partial (WHERE job_url IS NOT NULL) because job_url is nullable -- e.g. a manually-entered
-- application with no posting link -- and multiple NULLs must not collide.

CREATE UNIQUE INDEX IF NOT EXISTS idx_job_applications_job_url_unique
  ON job_applications(job_url)
  WHERE job_url IS NOT NULL;
