package retention

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

const (
	retentionPeriod = 7 * 24 * time.Hour
	tickInterval    = 24 * time.Hour
)

// Run starts the retention job loop. It blocks until ctx is cancelled.
func Run(ctx context.Context, pool *pgxpool.Pool) {
	ticker := time.NewTicker(tickInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := Compact(ctx, pool); err != nil {
				log.Printf("retention: compact error: %v", err)
			}
		}
	}
}

// Compact aggregates worker_metrics rows older than 7 days into worker_metrics_hourly
// (avg/max per worker per hour) and deletes the raw rows. Idempotent: running it
// twice produces the same result — ON CONFLICT DO NOTHING guards the aggregates and
// the raw rows are already gone on the second pass.
func Compact(ctx context.Context, pool *pgxpool.Pool) error {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	_, err = tx.Exec(ctx, `
		INSERT INTO worker_metrics_hourly (
			worker_id, hour,
			gpu_util_avg,    gpu_util_max,
			vram_used_avg,   vram_used_max,
			vram_free_avg,   vram_free_min,
			temperature_avg, temperature_max,
			power_avg,       power_max,
			cpu_avg,         cpu_max,
			ram_avg,         ram_max,
			sample_count
		)
		SELECT
			worker_id,
			date_trunc('hour', recorded_at)   AS hour,
			ROUND(AVG(gpu_util_pct))::INT,     MAX(gpu_util_pct),
			ROUND(AVG(vram_used_mb))::INT,     MAX(vram_used_mb),
			ROUND(AVG(vram_free_mb))::INT,     MIN(vram_free_mb),
			ROUND(AVG(temperature_c))::INT,    MAX(temperature_c),
			ROUND(AVG(power_w))::INT,          MAX(power_w),
			ROUND(AVG(cpu_pct))::INT,          MAX(cpu_pct),
			AVG(ram_used_gb),                  MAX(ram_used_gb),
			COUNT(*)::INT
		FROM worker_metrics
		WHERE recorded_at < now() - $1::interval
		GROUP BY worker_id, date_trunc('hour', recorded_at)
		ON CONFLICT (worker_id, hour) DO NOTHING`,
		retentionPeriod.String(),
	)
	if err != nil {
		return fmt.Errorf("aggregate metrics: %w", err)
	}

	tag, err := tx.Exec(ctx, `
		DELETE FROM worker_metrics
		WHERE recorded_at < now() - $1::interval`,
		retentionPeriod.String(),
	)
	if err != nil {
		return fmt.Errorf("delete raw metrics: %w", err)
	}

	if err := tx.Commit(ctx); err != nil {
		return err
	}

	if tag.RowsAffected() > 0 {
		log.Printf("retention: compacted %d raw metric rows", tag.RowsAffected())
	}
	return nil
}
