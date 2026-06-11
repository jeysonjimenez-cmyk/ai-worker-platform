package jobs

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Job struct {
	ID           string          `json:"id"`
	App          string          `json:"app"`
	Service      string          `json:"service"`
	Priority     int             `json:"priority"`
	Status       string          `json:"status"`
	Payload      json.RawMessage `json:"payload,omitempty"`
	Requirements json.RawMessage `json:"requirements,omitempty"`
	Routing      json.RawMessage `json:"routing,omitempty"`
	WorkerID     *string         `json:"worker_id,omitempty"`
	ProviderUsed *string         `json:"provider_used,omitempty"`
	Progress     int             `json:"progress"`
	Result       json.RawMessage `json:"result,omitempty"`
	CostUSD      *float64        `json:"cost_usd,omitempty"`
	ErrorMsg     *string         `json:"error_msg,omitempty"`
	WebhookURL   *string         `json:"webhook_url,omitempty"`
	WorkflowID   *string         `json:"workflow_id,omitempty"`
	RetryCount   int             `json:"retry_count"`
	MaxRetries   int             `json:"max_retries"`
	RetryAfter   *time.Time      `json:"retry_after,omitempty"`
	CreatedAt    time.Time       `json:"created_at"`
	StartedAt    *time.Time      `json:"started_at,omitempty"`
	CompletedAt  *time.Time      `json:"completed_at,omitempty"`
}

type CreateParams struct {
	App          string
	Service      string
	Priority     int
	Payload      json.RawMessage
	Requirements json.RawMessage
	Routing      json.RawMessage
	MaxRetries   int
	WebhookURL   *string
	WorkflowID   *string
}

func Create(ctx context.Context, pool *pgxpool.Pool, p CreateParams) (*Job, error) {
	var j Job
	err := pool.QueryRow(ctx, `
		INSERT INTO jobs (app, service, priority, payload, requirements, routing, max_retries, webhook_url, workflow_id)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
		RETURNING id, app, service, priority, status, payload, requirements, routing,
		          worker_id, provider_used, progress, result, cost_usd, error_msg,
		          webhook_url, workflow_id, retry_count, max_retries, retry_after,
		          created_at, started_at, completed_at`,
		p.App, p.Service, p.Priority, p.Payload, p.Requirements, p.Routing,
		p.MaxRetries, p.WebhookURL, p.WorkflowID,
	).Scan(
		&j.ID, &j.App, &j.Service, &j.Priority, &j.Status,
		&j.Payload, &j.Requirements, &j.Routing,
		&j.WorkerID, &j.ProviderUsed, &j.Progress, &j.Result, &j.CostUSD,
		&j.ErrorMsg, &j.WebhookURL, &j.WorkflowID, &j.RetryCount, &j.MaxRetries,
		&j.RetryAfter, &j.CreatedAt, &j.StartedAt, &j.CompletedAt,
	)
	if err != nil {
		return nil, fmt.Errorf("insert job: %w", err)
	}
	return &j, nil
}

func Get(ctx context.Context, pool *pgxpool.Pool, id string) (*Job, error) {
	var j Job
	err := pool.QueryRow(ctx, `
		SELECT id, app, service, priority, status, payload, requirements, routing,
		       worker_id, provider_used, progress, result, cost_usd, error_msg,
		       webhook_url, workflow_id, retry_count, max_retries, retry_after,
		       created_at, started_at, completed_at
		FROM jobs WHERE id = $1`, id,
	).Scan(
		&j.ID, &j.App, &j.Service, &j.Priority, &j.Status,
		&j.Payload, &j.Requirements, &j.Routing,
		&j.WorkerID, &j.ProviderUsed, &j.Progress, &j.Result, &j.CostUSD,
		&j.ErrorMsg, &j.WebhookURL, &j.WorkflowID, &j.RetryCount, &j.MaxRetries,
		&j.RetryAfter, &j.CreatedAt, &j.StartedAt, &j.CompletedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("get job: %w", err)
	}
	return &j, nil
}

// ReleaseVRAMReservation decrements vram_reserved_mb for the job's GPU reservation.
// Idempotent: sets jobs.vram_released = true; second call is a no-op.
// Must be called inside an existing transaction.
func ReleaseVRAMReservation(ctx context.Context, tx pgx.Tx, jobID string) error {
	// Mark released; if already released, skip.
	tag, err := tx.Exec(ctx,
		`UPDATE jobs SET vram_released = true WHERE id = $1 AND vram_released = false`,
		jobID,
	)
	if err != nil {
		return fmt.Errorf("mark vram_released: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return nil // already released
	}
	_, err = tx.Exec(ctx, `
		UPDATE gpus g
		SET vram_reserved_mb = GREATEST(0, g.vram_reserved_mb - COALESCE((j.requirements->>'min_vram_mb')::int, 0))
		FROM workers w
		JOIN jobs j ON j.id = $1
		WHERE j.worker_id = w.id AND w.gpu_id = g.id`,
		jobID,
	)
	return err
}

type UpdateProgressParams struct {
	JobID    string
	WorkerID string
	Progress int
	LogMsg   *string
	LogLevel *string
}

// UpdateProgress sets progress and optionally appends a log entry.
// Returns false if the job is not owned by workerID (fencing).
func UpdateProgress(ctx context.Context, pool *pgxpool.Pool, p UpdateProgressParams) (bool, error) {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer tx.Rollback(ctx)

	tag, err := tx.Exec(ctx,
		`UPDATE jobs SET progress = $1 WHERE id = $2 AND worker_id = $3 AND status = 'running'`,
		p.Progress, p.JobID, p.WorkerID,
	)
	if err != nil {
		return false, fmt.Errorf("update progress: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return false, nil
	}
	if p.LogMsg != nil {
		level := "info"
		if p.LogLevel != nil {
			level = *p.LogLevel
		}
		_, err = tx.Exec(ctx,
			`INSERT INTO job_logs (job_id, level, message) VALUES ($1, $2, $3)`,
			p.JobID, level, *p.LogMsg,
		)
		if err != nil {
			return false, fmt.Errorf("insert log: %w", err)
		}
	}
	return true, tx.Commit(ctx)
}

type CompleteParams struct {
	JobID    string
	WorkerID string
	Result   json.RawMessage
	CostUSD  *float64
	ErrMsg   *string // non-nil → status becomes "error"
}

// Complete transitions a job to done or error and releases VRAM.
// Returns false if the job is not owned by workerID (fencing).
func Complete(ctx context.Context, pool *pgxpool.Pool, p CompleteParams) (*Job, bool, error) {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return nil, false, err
	}
	defer tx.Rollback(ctx)

	var j Job
	status := "done"
	if p.ErrMsg != nil {
		status = "error"
	}
	err = tx.QueryRow(ctx, `
		UPDATE jobs
		SET status = $1, result = $2, cost_usd = $3, error_msg = $4, completed_at = now(), progress = 100
		WHERE id = $5 AND worker_id = $6 AND status = 'running'
		RETURNING id, app, service, priority, status, payload, requirements, routing,
		          worker_id, provider_used, progress, result, cost_usd, error_msg,
		          webhook_url, workflow_id, retry_count, max_retries, retry_after,
		          created_at, started_at, completed_at`,
		status, p.Result, p.CostUSD, p.ErrMsg, p.JobID, p.WorkerID,
	).Scan(
		&j.ID, &j.App, &j.Service, &j.Priority, &j.Status,
		&j.Payload, &j.Requirements, &j.Routing,
		&j.WorkerID, &j.ProviderUsed, &j.Progress, &j.Result, &j.CostUSD,
		&j.ErrorMsg, &j.WebhookURL, &j.WorkflowID, &j.RetryCount, &j.MaxRetries,
		&j.RetryAfter, &j.CreatedAt, &j.StartedAt, &j.CompletedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, fmt.Errorf("complete job: %w", err)
	}

	if err := ReleaseVRAMReservation(ctx, tx, p.JobID); err != nil {
		return nil, false, fmt.Errorf("release vram: %w", err)
	}

	// Handle retries if error
	if status == "error" && shouldRetry(j.RetryCount, j.MaxRetries) {
		retryAfter := time.Now().Add(nextRetryDelay(j.RetryCount))
		_, err = tx.Exec(ctx, `
			UPDATE jobs
			SET status = 'pending', worker_id = NULL, started_at = NULL,
			    retry_count = retry_count + 1, retry_after = $1
			WHERE id = $2`,
			retryAfter, p.JobID,
		)
		if err != nil {
			return nil, false, fmt.Errorf("schedule retry: %w", err)
		}
		j.Status = "pending"
		j.RetryAfter = &retryAfter
		j.RetryCount++
	}

	return &j, true, tx.Commit(ctx)
}

type CancelParams struct {
	JobID string
	AppID string
}

// Cancel cancels a job. Returns ("", nil) if not found/forbidden, status otherwise.
func Cancel(ctx context.Context, pool *pgxpool.Pool, p CancelParams) (string, error) {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)

	var status string
	err = tx.QueryRow(ctx, `SELECT status FROM jobs WHERE id = $1 AND app = $2`, p.JobID, p.AppID).Scan(&status)
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
		`UPDATE jobs SET status = 'cancelled', completed_at = now() WHERE id = $1`,
		p.JobID,
	)
	if err != nil {
		return "", fmt.Errorf("cancel job: %w", err)
	}

	if status == "running" {
		if err := ReleaseVRAMReservation(ctx, tx, p.JobID); err != nil {
			return "", fmt.Errorf("release vram on cancel: %w", err)
		}
	}

	return "cancelled", tx.Commit(ctx)
}

// RequeueTimedOutJob moves a running job back to pending and releases VRAM.
// Called by the heartbeat monitor.
func RequeueTimedOutJob(ctx context.Context, tx pgx.Tx, jobID string) error {
	if err := ReleaseVRAMReservation(ctx, tx, jobID); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, `
		UPDATE jobs
		SET status = 'pending', worker_id = NULL, started_at = NULL
		WHERE id = $1 AND status = 'running'`,
		jobID,
	)
	return err
}
