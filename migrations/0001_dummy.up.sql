-- Migration 0001: dummy table to validate migration tooling
CREATE TABLE IF NOT EXISTS _migration_test (
    id   SERIAL PRIMARY KEY,
    note TEXT NOT NULL DEFAULT 'ok'
);
