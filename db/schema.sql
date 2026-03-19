CREATE TABLE IF NOT EXISTS users (
    id               BIGSERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    marketing_opt_in BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS telegram_users (
    user_id           BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    telegram_user_id  BIGINT NOT NULL UNIQUE,
    chat_id           BIGINT NOT NULL,
    username          TEXT,
    first_name        TEXT,
    last_name         TEXT,
    language_code     TEXT,
    last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id               BIGSERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by_id    INTEGER REFERENCES auth_user(id) ON DELETE SET NULL,
    title            TEXT NOT NULL DEFAULT '',
    text             TEXT NOT NULL DEFAULT '',
    recipient_mode   TEXT NOT NULL CHECK (recipient_mode IN ('all_telegram','marketing_opt_in')) DEFAULT 'all_telegram',
    status           TEXT NOT NULL CHECK (status IN ('draft','queued','running','completed','failed','canceled')) DEFAULT 'draft',
    total_recipients INTEGER NOT NULL DEFAULT 0,
    sent_count       INTEGER NOT NULL DEFAULT 0,
    failed_count     INTEGER NOT NULL DEFAULT 0,
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    last_error       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS broadcast_attachments (
    id            BIGSERIAL PRIMARY KEY,
    broadcast_id  BIGINT NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    file          TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    content_type  TEXT NOT NULL DEFAULT '',
    size_bytes    BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS broadcast_deliveries (
    id                 BIGSERIAL PRIMARY KEY,
    broadcast_id       BIGINT NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    recipient_user_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    telegram_user_id   BIGINT NOT NULL,
    chat_id            BIGINT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('pending','running','sent','failed')) DEFAULT 'pending',
    attempts           INTEGER NOT NULL DEFAULT 0,
    run_after          TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by          TEXT,
    locked_at          TIMESTAMPTZ,
    sent_at            TIMESTAMPTZ,
    last_error         TEXT NOT NULL DEFAULT '',
    CONSTRAINT broadcast_delivery_unique_recipient UNIQUE (broadcast_id, recipient_user_id)
);

CREATE TABLE IF NOT EXISTS web_accounts (
    user_id        BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    email          TEXT UNIQUE,
    password_hash  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analysis_slots (
    slot_id          BIGSERIAL PRIMARY KEY,
    holder           TEXT NOT NULL CHECK (holder IN ('hold', 'free')),
    lease_until      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS download_jobs (
    id                 BIGSERIAL PRIMARY KEY,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    created_by_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_via        TEXT NOT NULL CHECK (created_via IN ('telegram','web')),

    telegram_user_id   BIGINT,
    telegram_chat_id   BIGINT,
    progress_msg_id    BIGINT,

    source_url         TEXT NOT NULL,
    title              TEXT,
    duration_seconds   INT,
    is_short           BOOLEAN NOT NULL DEFAULT false,

    requested_quality  TEXT,
    requested_audio    TEXT,
    selected_format    JSONB,
    selected_audio     JSONB,

    status             TEXT NOT NULL CHECK (status IN ('queued','running','done','failed','canceled')) DEFAULT 'queued',
    priority           INT NOT NULL DEFAULT 0,
    progress           INT NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    stage              TEXT,

    attempts           INT NOT NULL DEFAULT 0,
    run_after          TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by          TEXT,
    locked_at          TIMESTAMPTZ,

    result_path        TEXT,
    result_size_bytes  BIGINT,
    result_meta        JSONB,

    error_code         TEXT,
    error_message      TEXT
);

ALTER TABLE download_jobs ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT;
ALTER TABLE download_jobs ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT;
ALTER TABLE download_jobs ADD COLUMN IF NOT EXISTS progress_msg_id BIGINT;
ALTER TABLE download_jobs ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE download_jobs ADD COLUMN IF NOT EXISTS duration_seconds INT;
ALTER TABLE download_jobs ADD COLUMN IF NOT EXISTS is_short BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE download_jobs ADD COLUMN IF NOT EXISTS selected_format JSONB;
ALTER TABLE download_jobs ADD COLUMN IF NOT EXISTS selected_audio JSONB;

CREATE INDEX IF NOT EXISTS idx_download_jobs_status_created_at
    ON download_jobs(status, created_at);

CREATE INDEX IF NOT EXISTS idx_download_jobs_queue
    ON download_jobs(is_short, status, priority, created_at);

CREATE INDEX IF NOT EXISTS idx_download_jobs_run_after
    ON download_jobs(run_after);

CREATE INDEX IF NOT EXISTS idx_broadcasts_status_created_at
    ON broadcasts(status, created_at);

CREATE INDEX IF NOT EXISTS idx_broadcast_deliveries_queue
    ON broadcast_deliveries(status, run_after, id);

CREATE INDEX IF NOT EXISTS idx_broadcast_deliveries_broadcast_id
    ON broadcast_deliveries(broadcast_id);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_download_jobs_updated_at ON download_jobs;
CREATE TRIGGER trg_download_jobs_updated_at
BEFORE UPDATE ON download_jobs
FOR EACH ROW EXECUTE PROCEDURE set_updated_at();

DROP TRIGGER IF EXISTS trg_broadcasts_updated_at ON broadcasts;
CREATE TRIGGER trg_broadcasts_updated_at
BEFORE UPDATE ON broadcasts
FOR EACH ROW EXECUTE PROCEDURE set_updated_at();
