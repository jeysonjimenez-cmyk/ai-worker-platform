package monitor

import (
	"context"
	"log"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/jobs"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/workers"
)

const (
	heartbeatTimeout = 60 * time.Second
	tickInterval     = 10 * time.Second
)

// Run starts the heartbeat monitor loop. It blocks until ctx is cancelled.
func Run(ctx context.Context, pool *pgxpool.Pool) {
	ticker := time.NewTicker(tickInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := tick(ctx, pool); err != nil {
				log.Printf("monitor tick error: %v", err)
			}
		}
	}
}

func tick(ctx context.Context, pool *pgxpool.Pool) error {
	// Heartbeat check: workers that stopped reporting are marked offline and their jobs requeued.
	timedOut, err := workers.TimedOutWorkers(ctx, pool, heartbeatTimeout)
	if err != nil {
		return err
	}
	for _, id := range timedOut {
		if err := workers.HandleWorkerTimeout(ctx, pool, id); err != nil {
			log.Printf("monitor: handle timeout for worker %s: %v", id, err)
		} else {
			log.Printf("monitor: worker %s timed out, jobs requeued", id)
		}
	}

	// Job max-duration check: running jobs that exceeded their service's time limit.
	overdueIDs, err := jobs.FailTimedOutJobs(ctx, pool, jobs.ServiceMaxDurations())
	if err != nil {
		log.Printf("monitor: fail timed-out jobs: %v", err)
		return nil
	}
	for _, id := range overdueIDs {
		log.Printf("monitor: job %s exceeded max duration, transitioned to error/retry", id)
	}
	return nil
}
