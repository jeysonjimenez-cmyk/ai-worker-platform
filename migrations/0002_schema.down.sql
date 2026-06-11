-- Migration 0002: drop platform schema

DROP TABLE IF EXISTS worker_metrics;
DROP TABLE IF EXISTS job_files;
DROP TABLE IF EXISTS job_logs;
DROP TABLE IF EXISTS jobs;
ALTER TABLE workers DROP CONSTRAINT IF EXISTS workers_gpu_id_fk;
DROP TABLE IF EXISTS workers;
DROP TABLE IF EXISTS gpus;
DROP TABLE IF EXISTS apps;
