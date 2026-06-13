-- Migration 0003: aggregated metrics table for retention job

CREATE TABLE worker_metrics_hourly (
    worker_id       TEXT NOT NULL REFERENCES workers(id),
    hour            TIMESTAMPTZ NOT NULL,
    gpu_util_avg    INT,
    gpu_util_max    INT,
    vram_used_avg   INT,
    vram_used_max   INT,
    vram_free_avg   INT,
    vram_free_min   INT,
    temperature_avg INT,
    temperature_max INT,
    power_avg       INT,
    power_max       INT,
    cpu_avg         INT,
    cpu_max         INT,
    ram_avg         FLOAT,
    ram_max         FLOAT,
    sample_count    INT NOT NULL,
    PRIMARY KEY (worker_id, hour)
);
