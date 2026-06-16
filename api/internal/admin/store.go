package admin

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/jobs"
)

type WorkerSummary struct {
	ID            string     `json:"id"`
	Hostname      string     `json:"hostname"`
	Status        string     `json:"status"`
	GPUID         *string    `json:"gpu_id,omitempty"`
	CurrentJobID  *string    `json:"current_job_id,omitempty"`
	LastHeartbeat *time.Time `json:"last_heartbeat,omitempty"`
}

type JobSummary struct {
	ID         string    `json:"id"`
	App        string    `json:"app"`
	Service    string    `json:"service"`
	Status     string    `json:"status"`
	WorkerID   *string   `json:"worker_id,omitempty"`
	Priority   int       `json:"priority"`
	WorkflowID *string   `json:"workflow_id,omitempty"`
	CreatedAt  time.Time `json:"created_at"`
}

// JobDetail is the admin view of a job. vram_released and other ledger fields are intentionally excluded (R1).
type JobDetail struct {
	ID          string          `json:"id"`
	App         string          `json:"app"`
	Service     string          `json:"service"`
	Priority    int             `json:"priority"`
	Status      string          `json:"status"`
	Payload     json.RawMessage `json:"payload,omitempty"`
	ErrorMsg    *string         `json:"error_msg,omitempty"`
	WorkerID    *string         `json:"worker_id,omitempty"`
	RetryCount  int             `json:"retry_count"`
	MaxRetries  int             `json:"max_retries"`
	WorkflowID  *string         `json:"workflow_id,omitempty"`
	CreatedAt   time.Time       `json:"created_at"`
	StartedAt   *time.Time      `json:"started_at,omitempty"`
	CompletedAt *time.Time      `json:"completed_at,omitempty"`
}

func ListWorkers(ctx context.Context, pool *pgxpool.Pool) ([]WorkerSummary, error) {
	rows, err := pool.Query(ctx, `
		SELECT id, hostname, status, gpu_id, current_job_id, last_heartbeat
		FROM workers
		ORDER BY id`)
	if err != nil {
		return nil, fmt.Errorf("list workers: %w", err)
	}
	defer rows.Close()

	var ws []WorkerSummary
	for rows.Next() {
		var w WorkerSummary
		if err := rows.Scan(&w.ID, &w.Hostname, &w.Status, &w.GPUID, &w.CurrentJobID, &w.LastHeartbeat); err != nil {
			return nil, fmt.Errorf("scan worker: %w", err)
		}
		ws = append(ws, w)
	}
	return ws, rows.Err()
}

// ListJobs returns jobs filtered by status (empty string = all), ordered by created_at DESC, limited to 200.
func ListJobs(ctx context.Context, pool *pgxpool.Pool, status string) ([]JobSummary, error) {
	var (
		rows pgx.Rows
		err  error
	)
	if status == "" {
		rows, err = pool.Query(ctx, `
			SELECT id, app, service, status, worker_id, priority, workflow_id, created_at
			FROM jobs
			ORDER BY created_at DESC
			LIMIT 200`)
	} else {
		rows, err = pool.Query(ctx, `
			SELECT id, app, service, status, worker_id, priority, workflow_id, created_at
			FROM jobs
			WHERE status = $1
			ORDER BY created_at DESC
			LIMIT 200`, status)
	}
	if err != nil {
		return nil, fmt.Errorf("list jobs: %w", err)
	}
	defer rows.Close()

	var js []JobSummary
	for rows.Next() {
		var j JobSummary
		if err := rows.Scan(&j.ID, &j.App, &j.Service, &j.Status, &j.WorkerID, &j.Priority, &j.WorkflowID, &j.CreatedAt); err != nil {
			return nil, fmt.Errorf("scan job: %w", err)
		}
		js = append(js, j)
	}
	return js, rows.Err()
}

// GetJobDetail returns the admin view of a job, excluding internal ledger fields (R1).
// Returns nil if the job does not exist.
func GetJobDetail(ctx context.Context, pool *pgxpool.Pool, id string) (*JobDetail, error) {
	var j JobDetail
	err := pool.QueryRow(ctx, `
		SELECT id, app, service, priority, status, payload, error_msg,
		       worker_id, retry_count, max_retries, workflow_id,
		       created_at, started_at, completed_at
		FROM jobs WHERE id = $1`, id,
	).Scan(
		&j.ID, &j.App, &j.Service, &j.Priority, &j.Status,
		&j.Payload, &j.ErrorMsg, &j.WorkerID,
		&j.RetryCount, &j.MaxRetries, &j.WorkflowID,
		&j.CreatedAt, &j.StartedAt, &j.CompletedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("get job detail: %w", err)
	}
	return &j, nil
}

// AdminCancelJob cancels a job without app-scoping (admin, cross-app).
// Returns "" if not found, the current status if already terminal, "cancelled" on success.
func AdminCancelJob(ctx context.Context, pool *pgxpool.Pool, jobID string) (string, error) {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)

	var status string
	err = tx.QueryRow(ctx, `SELECT status FROM jobs WHERE id = $1 FOR UPDATE`, jobID).Scan(&status)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("get job status: %w", err)
	}
	if status == "done" || status == "error" || status == "cancelled" {
		return status, nil
	}

	_, err = tx.Exec(ctx,
		`UPDATE jobs SET status = 'cancelled', completed_at = now() WHERE id = $1`, jobID)
	if err != nil {
		return "", fmt.Errorf("cancel job: %w", err)
	}

	if status == "running" {
		if err := jobs.ReleaseVRAMReservation(ctx, tx, jobID); err != nil {
			return "", fmt.Errorf("release vram on cancel: %w", err)
		}
	}

	return "cancelled", tx.Commit(ctx)
}

// AdminRetryJob manually retries a job in error state: error → pending.
// Returns "" if not found, the current status if not in error, "pending" on success.
func AdminRetryJob(ctx context.Context, pool *pgxpool.Pool, jobID string) (string, error) {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)

	var status string
	err = tx.QueryRow(ctx, `SELECT status FROM jobs WHERE id = $1 FOR UPDATE`, jobID).Scan(&status)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("get job status: %w", err)
	}
	if status != "error" {
		return status, nil
	}

	// VRAM was already released when the job entered error state; no release needed here.
	_, err = tx.Exec(ctx, `
		UPDATE jobs
		SET status = 'pending', worker_id = NULL, started_at = NULL,
		    error_msg = NULL, retry_count = 0, completed_at = NULL
		WHERE id = $1`, jobID)
	if err != nil {
		return "", fmt.Errorf("retry job: %w", err)
	}

	return "pending", tx.Commit(ctx)
}
