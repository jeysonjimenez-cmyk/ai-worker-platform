package workers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/jobs"
)

type Worker struct {
	ID           string          `json:"id"`
	Hostname     string          `json:"hostname"`
	Status       string          `json:"status"`
	Capabilities json.RawMessage `json:"capabilities"`
	GPUID        *string         `json:"gpu_id,omitempty"`
	CurrentJobID *string         `json:"current_job_id,omitempty"`
	LastHeartbeat *time.Time     `json:"last_heartbeat,omitempty"`
	RegisteredAt  time.Time      `json:"registered_at"`
}

type RegisterParams struct {
	ID           string
	Hostname     string
	Capabilities json.RawMessage
	APIKey       string
	GPUID        *string // explicit gpu_id from client; falls back to hostname+"/gpu-0" if nil
}

type capabilities struct {
	Services    []string `json:"services"`
	CUDA        bool     `json:"cuda"`
	VRAMTotalMB int      `json:"vram_total_mb"`
}

func Register(ctx context.Context, pool *pgxpool.Pool, p RegisterParams) (*Worker, error) {
	var caps capabilities
	json.Unmarshal(p.Capabilities, &caps)

	tx, err := pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	// Upsert GPU row if worker has a GPU.
	var gpuID *string
	if caps.CUDA && caps.VRAMTotalMB > 0 {
		var id string
		if p.GPUID != nil && *p.GPUID != "" {
			id = *p.GPUID
		} else {
			id = p.Hostname + "/gpu-0"
		}
		gpuID = &id
		_, err = tx.Exec(ctx, `
			INSERT INTO gpus (id, hostname, vram_total_mb)
			VALUES ($1, $2, $3)
			ON CONFLICT (id) DO UPDATE SET vram_total_mb = EXCLUDED.vram_total_mb`,
			id, p.Hostname, caps.VRAMTotalMB,
		)
		if err != nil {
			return nil, fmt.Errorf("upsert gpu: %w", err)
		}
	}

	// Upsert worker row (api_key cannot be changed on re-register).
	_, err = tx.Exec(ctx, `
		INSERT INTO workers (id, hostname, status, capabilities, gpu_id, api_key, last_heartbeat, registered_at)
		VALUES ($1, $2, 'online', $3, $4, $5, now(), now())
		ON CONFLICT (id) DO UPDATE
		SET hostname = EXCLUDED.hostname,
		    status = 'online',
		    capabilities = EXCLUDED.capabilities,
		    gpu_id = EXCLUDED.gpu_id,
		    last_heartbeat = now()`,
		p.ID, p.Hostname, p.Capabilities, gpuID, p.APIKey,
	)
	if err != nil {
		return nil, fmt.Errorf("upsert worker: %w", err)
	}

	var w Worker
	err = tx.QueryRow(ctx, `
		SELECT id, hostname, status, capabilities, gpu_id, current_job_id, last_heartbeat, registered_at
		FROM workers WHERE id = $1`, p.ID,
	).Scan(&w.ID, &w.Hostname, &w.Status, &w.Capabilities, &w.GPUID, &w.CurrentJobID, &w.LastHeartbeat, &w.RegisteredAt)
	if err != nil {
		return nil, fmt.Errorf("read worker: %w", err)
	}

	return &w, tx.Commit(ctx)
}

func Heartbeat(ctx context.Context, pool *pgxpool.Pool, workerID string) error {
	tag, err := pool.Exec(ctx,
		`UPDATE workers SET last_heartbeat = now(), status = 'online' WHERE id = $1`,
		workerID,
	)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return pgx.ErrNoRows
	}
	return nil
}

// Claim attempts to assign the highest-priority eligible pending job to the worker.
// Returns nil job if no eligible job is available.
func Claim(ctx context.Context, pool *pgxpool.Pool, workerID string, vramMarginMB int) (*jobs.Job, error) {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	// Load worker capabilities and GPU.
	var w Worker
	err = tx.QueryRow(ctx,
		`SELECT id, hostname, status, capabilities, gpu_id FROM workers WHERE id = $1`,
		workerID,
	).Scan(&w.ID, &w.Hostname, &w.Status, &w.Capabilities, &w.GPUID)
	if err != nil {
		return nil, fmt.Errorf("load worker: %w", err)
	}

	var caps capabilities
	json.Unmarshal(w.Capabilities, &caps)

	// Build services filter as a JSON array for the query.
	servicesJSON, _ := json.Marshal(caps.Services)

	// Select the best eligible pending job (SKIP LOCKED).
	var j jobs.Job
	err = tx.QueryRow(ctx, `
		SELECT id, app, service, priority, status, payload, requirements, routing,
		       worker_id, provider_used, progress, result, cost_usd, error_msg,
		       webhook_url, workflow_id, retry_count, max_retries, retry_after,
		       created_at, started_at, completed_at
		FROM jobs
		WHERE status = 'pending'
		  AND (retry_after IS NULL OR retry_after <= now())
		  AND service = ANY(
		    SELECT jsonb_array_elements_text($1::jsonb)
		  )
		ORDER BY priority ASC, created_at ASC
		FOR UPDATE SKIP LOCKED
		LIMIT 1`,
		string(servicesJSON),
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
		return nil, fmt.Errorf("select job: %w", err)
	}

	// Atomically reserve VRAM if the job requires it and the worker has a GPU.
	var minVRAM int
	if j.Requirements != nil {
		var req struct {
			MinVRAMMB int `json:"min_vram_mb"`
		}
		json.Unmarshal(j.Requirements, &req)
		minVRAM = req.MinVRAMMB
	}

	if minVRAM > 0 && w.GPUID != nil {
		var gpuID string
		tx.QueryRow(ctx, `SELECT id FROM gpus
			WHERE id = $1
			  AND vram_reserved_mb + $2 <= vram_total_mb - $3
			FOR UPDATE`,
			*w.GPUID, minVRAM, vramMarginMB,
		).Scan(&gpuID)

		if gpuID == "" {
			// Not enough VRAM — roll back and return no job.
			return nil, nil
		}
		_, err = tx.Exec(ctx,
			`UPDATE gpus SET vram_reserved_mb = vram_reserved_mb + $1 WHERE id = $2`,
			minVRAM, *w.GPUID,
		)
		if err != nil {
			return nil, fmt.Errorf("reserve vram: %w", err)
		}
	}

	// Transition job to running.
	err = tx.QueryRow(ctx, `
		UPDATE jobs
		SET status = 'running', worker_id = $1, started_at = now(), vram_released = false
		WHERE id = $2
		RETURNING id, app, service, priority, status, payload, requirements, routing,
		          worker_id, provider_used, progress, result, cost_usd, error_msg,
		          webhook_url, workflow_id, retry_count, max_retries, retry_after,
		          created_at, started_at, completed_at`,
		workerID, j.ID,
	).Scan(
		&j.ID, &j.App, &j.Service, &j.Priority, &j.Status,
		&j.Payload, &j.Requirements, &j.Routing,
		&j.WorkerID, &j.ProviderUsed, &j.Progress, &j.Result, &j.CostUSD,
		&j.ErrorMsg, &j.WebhookURL, &j.WorkflowID, &j.RetryCount, &j.MaxRetries,
		&j.RetryAfter, &j.CreatedAt, &j.StartedAt, &j.CompletedAt,
	)
	if err != nil {
		return nil, fmt.Errorf("assign job: %w", err)
	}

	// Update worker's current_job_id.
	tx.Exec(ctx, `UPDATE workers SET current_job_id = $1 WHERE id = $2`, j.ID, workerID)

	return &j, tx.Commit(ctx)
}

// ReleaseModelVRAM frees VRAM when a worker explicitly unloads a model.
// It finds any completed/errored/cancelled job that still has vram_released=false for this worker.
func ReleaseModelVRAM(ctx context.Context, pool *pgxpool.Pool, workerID string) error {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	// Find jobs for this worker that still hold a VRAM reservation.
	rows, err := tx.Query(ctx, `
		SELECT id FROM jobs
		WHERE worker_id = $1 AND vram_released = false
		  AND status IN ('done', 'error', 'cancelled')`,
		workerID,
	)
	if err != nil {
		return err
	}
	var jobIDs []string
	for rows.Next() {
		var id string
		rows.Scan(&id)
		jobIDs = append(jobIDs, id)
	}
	rows.Close()

	for _, id := range jobIDs {
		if err := jobs.ReleaseVRAMReservation(ctx, tx, id); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

type MetricSample struct {
	GPUUtilPct   *int     `json:"gpu_util_pct"`
	VRAMTotalMB  *int     `json:"vram_total_mb"`
	VRAMUsedMB   *int     `json:"vram_used_mb"`
	VRAMFreeMB   *int     `json:"vram_free_mb"`
	TemperatureC *int     `json:"temperature_c"`
	PowerW       *int     `json:"power_w"`
	CPUPct       *int     `json:"cpu_pct"`
	RAMUsedGB    *float64 `json:"ram_used_gb"`
	RecordedAt   time.Time `json:"recorded_at"`
}

// IngestMetrics writes a batch of metric samples for a worker.
// recorded_at comes from the client (capture time), not the server clock.
// After persisting, it logs a warning if vram_free_mb deviates from the ledger by more than driftMarginMB.
func IngestMetrics(ctx context.Context, pool *pgxpool.Pool, workerID string, samples []MetricSample, driftMarginMB int) error {
	if len(samples) == 0 {
		return nil
	}
	tx, err := pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	for _, s := range samples {
		_, err := tx.Exec(ctx, `
			INSERT INTO worker_metrics
				(worker_id, gpu_util_pct, vram_total_mb, vram_used_mb, vram_free_mb,
				 temperature_c, power_w, cpu_pct, ram_used_gb, recorded_at)
			VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)`,
			workerID,
			s.GPUUtilPct, s.VRAMTotalMB, s.VRAMUsedMB, s.VRAMFreeMB,
			s.TemperatureC, s.PowerW, s.CPUPct, s.RAMUsedGB, s.RecordedAt,
		)
		if err != nil {
			return fmt.Errorf("insert metric: %w", err)
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return err
	}
	checkVRAMDrift(ctx, pool, workerID, samples, driftMarginMB)
	return nil
}

// vramDriftExceeded reports whether the absolute difference between the ledger-predicted
// free VRAM and the hardware-reported value exceeds the margin.
func vramDriftExceeded(ledgerFreeMB, reportedFreeMB, marginMB int) bool {
	diff := ledgerFreeMB - reportedFreeMB
	if diff < 0 {
		diff = -diff
	}
	return diff > marginMB
}

// checkVRAMDrift logs a warning if the most recent vram_free_mb in the batch diverges
// from the ledger prediction (vram_total_mb - vram_reserved_mb) by more than marginMB.
// Best-effort: silently skips if the worker has no GPU or the ledger row is missing.
func checkVRAMDrift(ctx context.Context, pool *pgxpool.Pool, workerID string, samples []MetricSample, marginMB int) {
	var reportedFree *int
	for i := len(samples) - 1; i >= 0; i-- {
		if samples[i].VRAMFreeMB != nil {
			reportedFree = samples[i].VRAMFreeMB
			break
		}
	}
	if reportedFree == nil {
		return
	}

	var gpuID string
	var vramTotal, vramReserved int
	err := pool.QueryRow(ctx, `
		SELECT g.id, g.vram_total_mb, g.vram_reserved_mb
		FROM workers w
		JOIN gpus g ON g.id = w.gpu_id
		WHERE w.id = $1`, workerID,
	).Scan(&gpuID, &vramTotal, &vramReserved)
	if err != nil {
		return
	}

	ledgerFree := vramTotal - vramReserved
	if vramDriftExceeded(ledgerFree, *reportedFree, marginMB) {
		diff := ledgerFree - *reportedFree
		if diff < 0 {
			diff = -diff
		}
		log.Printf("WARN vram drift: gpu_id=%s ledger_free_mb=%d reported_free_mb=%d delta_mb=%d margin_mb=%d",
			gpuID, ledgerFree, *reportedFree, diff, marginMB)
	}
}

// TimedOutWorkers returns worker IDs whose last_heartbeat is older than timeout.
func TimedOutWorkers(ctx context.Context, pool *pgxpool.Pool, timeout time.Duration) ([]string, error) {
	rows, err := pool.Query(ctx, `
		SELECT id FROM workers
		WHERE status = 'online' AND last_heartbeat < now() - $1::interval`,
		timeout.String(),
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var ids []string
	for rows.Next() {
		var id string
		rows.Scan(&id)
		ids = append(ids, id)
	}
	return ids, nil
}

// HandleWorkerTimeout marks a worker offline and requeues its running jobs.
func HandleWorkerTimeout(ctx context.Context, pool *pgxpool.Pool, workerID string) error {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	// Find running jobs for this worker.
	rows, err := tx.Query(ctx,
		`SELECT id FROM jobs WHERE worker_id = $1 AND status = 'running'`,
		workerID,
	)
	if err != nil {
		return err
	}
	var jobIDs []string
	for rows.Next() {
		var id string
		rows.Scan(&id)
		jobIDs = append(jobIDs, id)
	}
	rows.Close()

	for _, id := range jobIDs {
		if err := jobs.RequeueTimedOutJob(ctx, tx, id); err != nil {
			return fmt.Errorf("requeue job %s: %w", id, err)
		}
	}

	_, err = tx.Exec(ctx,
		`UPDATE workers SET status = 'offline', current_job_id = NULL WHERE id = $1`,
		workerID,
	)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}
