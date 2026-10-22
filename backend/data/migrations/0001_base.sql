-- Version 1 of the job store schema (app/storage/sql.py), the same for SQLite
-- and Postgres. Applied once per database by the migration runner. IF NOT EXISTS
-- keeps it safe on SQLite files created before schema versions existed.
-- Only search/filter fields are real columns, the full form group (with its
-- Commitment) is stored as JSON. No images are stored here.
-- Comments must not contain semicolons: the runner splits statements on them.

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,              -- ISO 8601, UTC
    status       TEXT NOT NULL,
    source_file  TEXT,                       -- stored file name, not the user's path
    form_type    TEXT,                       -- shared by all forms of the job, else NULL
    sender       TEXT,                       -- shared by all forms of the job, else NULL
    error        TEXT,                       -- exception type only, never field values
    needs_review INTEGER NOT NULL DEFAULT 0,
    irrelevant_pages_json TEXT NOT NULL DEFAULT '[]',  -- pages that belong to no form (file + page index)
    split_vlm_calls INTEGER NOT NULL DEFAULT 0          -- page pairs sent to the provider by segmentation
);

CREATE INDEX IF NOT EXISTS jobs_created_at ON jobs (created_at);

CREATE TABLE IF NOT EXISTS commitments (
    id           TEXT PRIMARY KEY,           -- FormGroup.id
    job_id       TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,           -- order of the form inside the job
    data_json    TEXT NOT NULL,              -- FormGroup JSON, including the full Commitment
    needs_review INTEGER NOT NULL,
    UNIQUE (job_id, seq)
);
