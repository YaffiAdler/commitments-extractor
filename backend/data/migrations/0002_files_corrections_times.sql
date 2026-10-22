-- Version 2: uploaded files get their own table (a job may hold several),
-- manual corrections are kept, and processing start/end times are stored.
-- Comments must not contain semicolons: the runner splits statements on them.

CREATE TABLE job_files (
    job_id       TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,           -- order of the file inside the job
    stored_name  TEXT NOT NULL,              -- generated name, not the user's path
    PRIMARY KEY (job_id, seq)
);

INSERT INTO job_files (job_id, seq, stored_name)
    SELECT id, 0, source_file FROM jobs WHERE source_file IS NOT NULL;

ALTER TABLE jobs DROP COLUMN source_file;

ALTER TABLE jobs ADD COLUMN started_at TEXT;    -- ISO 8601, UTC
ALTER TABLE jobs ADD COLUMN finished_at TEXT;   -- ISO 8601, UTC

CREATE TABLE corrections (
    job_id       TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,           -- order of the correction inside the job
    group_id     TEXT NOT NULL,              -- FormGroup.id (no foreign key: commitments rows are rewritten)
    field        TEXT NOT NULL,              -- Commitment field name
    corrected_at TEXT NOT NULL,              -- ISO 8601, UTC
    data_json    TEXT NOT NULL,              -- Correction JSON (holds the previous raw value: patient data)
    PRIMARY KEY (job_id, seq)
);
