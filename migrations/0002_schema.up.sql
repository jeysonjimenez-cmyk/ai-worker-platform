-- Migration 0002: platform schema

CREATE TABLE apps (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name          TEXT NOT NULL,
    api_key       TEXT NOT NULL UNIQUE,
    max_daily_usd NUMERIC(10,2),
    active        BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workers (
    id             TEXT PRIMARY KEY,
    hostname       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'offline',
    capabilities   JSONB NOT NULL DEFAULT '{}',
    gpu_id         TEXT,
    current_job_id TEXT,
    api_key        TEXT NOT NULL UNIQUE,
    last_heartbeat TIMESTAMPTZ,
    registered_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE gpus (
    id               TEXT PRIMARY KEY,
    hostname         TEXT NOT NULL,
    vram_total_mb    INT NOT NULL,
    vram_reserved_mb INT NOT NULL DEFAULT 0
);

ALTER TABLE workers ADD CONSTRAINT workers_gpu_id_fk
    FOREIGN KEY (gpu_id) REFERENCES gpus(id);

CREATE TABLE jobs (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    app             TEXT NOT NULL,
    service         TEXT NOT NULL,
    priority        INT NOT NULL DEFAULT 5,
    status          TEXT NOT NULL DEFAULT 'pending',
    payload         JSONB,
    requirements    JSONB,
    routing         JSONB,
    worker_id       TEXT REFERENCES workers(id),
    provider_used   TEXT,
    progress        INT NOT NULL DEFAULT 0,
    result          JSONB,
    cost_usd        NUMERIC(10,6),
    error_msg       TEXT,
    webhook_url     TEXT,
    workflow_id     TEXT,
    retry_count     INT NOT NULL DEFAULT 0,
    max_retries     INT NOT NULL DEFAULT 3,
    retry_delay_sec INT NOT NULL DEFAULT 30,
    retry_after     TIMESTAMPTZ,
    vram_released   BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX jobs_claim_idx ON jobs (status, priority, created_at);
CREATE INDEX jobs_worker_idx ON jobs (worker_id);

CREATE TABLE job_logs (
    id         BIGSERIAL PRIMARY KEY,
    job_id     TEXT NOT NULL REFERENCES jobs(id),
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE job_files (
    job_id     TEXT NOT NULL REFERENCES jobs(id),
    filename   TEXT NOT NULL,
    path       TEXT NOT NULL,
    size_bytes BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, filename)
);

CREATE TABLE worker_metrics (
    id           BIGSERIAL PRIMARY KEY,
    worker_id    TEXT NOT NULL REFERENCES workers(id),
    gpu_util_pct INT,
    vram_total_mb INT,
    vram_used_mb  INT,
    vram_free_mb  INT,
    temperature_c INT,
    power_w       INT,
    cpu_pct       INT,
    ram_used_gb   FLOAT,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX worker_metrics_lookup_idx ON worker_metrics (worker_id, recorded_at DESC);
