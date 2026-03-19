CREATE TABLE IF NOT EXISTS users (
  id            BIGSERIAL PRIMARY KEY,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  marketing_opt_in BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS telegram_users (
  user_id           BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  telegram_user_id  BIGINT NOT NULL UNIQUE,
  chat_id           BIGINT NOT NULL,
  username          TEXT,
  first_name        TEXT,
  last_name         TEXT,
  last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS download_jobs (
  id              BIGSERIAL PRIMARY KEY,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  created_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
  created_via     TEXT NOT NULL CHECK (created_via IN ('telegram','web')),

  source_url      TEXT NOT NULL,
  title           TEXT,

  requested_quality TEXT,
  requested_audio   TEXT,

  status          TEXT NOT NULL CHECK (status IN ('queued','running','done','failed','canceled')) DEFAULT 'queued',
  progress        REAL NOT NULL DEFAULT 0,

  attempts        INT NOT NULL DEFAULT 0,
  max_attempts    INT NOT NULL DEFAULT 3,
  priority        INT NOT NULL DEFAULT 0,
  run_after       TIMESTAMPTZ NOT NULL DEFAULT now(),

  locked_by       TEXT,
  locked_at       TIMESTAMPTZ,
  locked_until    TIMESTAMPTZ,

  result_path     TEXT,
  result_size_bytes BIGINT,

  error_code      TEXT,
  error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_download_jobs_status_run_after
  ON download_jobs(status, run_after, priority DESC, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_download_jobs_user_created
  ON download_jobs(created_by_user_id, created_at DESC);
