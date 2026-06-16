package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	adminh "github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/admin"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/auth"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/config"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/db"
	jobsh "github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/jobs"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/monitor"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/retention"
	workersh "github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/workers"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/webhook"
)

// withAdminCORS allows the dashboard (served from a different origin/port on the VPS)
// to call /admin/* from the browser. It must wrap the whole mux, not individual handlers:
// ServeMux returns 405 for an OPTIONS preflight before any handler-level middleware runs.
func withAdminCORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/admin/") {
			w.Header().Set("Access-Control-Allow-Origin", "*")
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "X-Admin-Key, Content-Type")
			if r.Method == http.MethodOptions {
				w.WriteHeader(http.StatusNoContent)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("config: %v", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	pool, err := db.New(ctx, cfg.DatabaseURL)
	if err != nil {
		log.Fatalf("db: %v", err)
	}
	defer pool.Close()

	dispatcher := webhook.New()
	jobsHandler := jobsh.NewHandler(pool, dispatcher, cfg.FileServerURL)
	workersHandler := workersh.NewHandler(pool, cfg.VRAMMarginMB, cfg.VRAMDriftMarginMB)
	adminHandler := adminh.NewHandler(pool)

	mux := http.NewServeMux()

	// Health check — no auth.
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		if err := pool.Ping(r.Context()); err != nil {
			http.Error(w, `{"status":"degraded"}`, http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"status":"ok"}`))
	})

	// App endpoints.
	appMW := func(h http.HandlerFunc) http.Handler { return auth.RequireApp(pool, h) }
	mux.Handle("POST /ai/jobs", appMW(jobsHandler.Create))
	mux.Handle("GET /ai/jobs/{id}", appMW(jobsHandler.GetByID))
	mux.Handle("GET /ai/jobs/{id}/files/{filename}", appMW(jobsHandler.GetFile))
	mux.Handle("POST /ai/jobs/{id}/cancel", appMW(jobsHandler.Cancel))

	// Admin endpoints.
	adminMW := func(h http.HandlerFunc) http.Handler { return auth.RequireAdmin(cfg.AdminAPIKey, h) }
	mux.Handle("GET /admin/workers", adminMW(adminHandler.ListWorkers))
	mux.Handle("GET /admin/jobs", adminMW(adminHandler.ListJobs))
	mux.Handle("GET /admin/jobs/{id}", adminMW(adminHandler.GetJobDetail))
	mux.Handle("POST /admin/jobs/{id}/cancel", adminMW(adminHandler.CancelJob))
	mux.Handle("POST /admin/jobs/{id}/retry", adminMW(adminHandler.RetryJob))

	// Worker endpoints.
	workerMW := func(h http.HandlerFunc) http.Handler { return auth.RequireWorker(pool, h) }
	// Register uses admin auth: workers are provisioned by the operator.
	mux.Handle("POST /workers/register", adminMW(workersHandler.Register))
	mux.Handle("POST /workers/{id}/heartbeat", workerMW(workersHandler.Heartbeat))
	mux.Handle("POST /workers/{id}/claim", workerMW(workersHandler.Claim))
	mux.Handle("POST /workers/{id}/unload-model", workerMW(workersHandler.UnloadModel))
	mux.Handle("POST /workers/{id}/metrics", workerMW(workersHandler.IngestMetrics))
	mux.Handle("PATCH /ai/jobs/{id}/progress", workerMW(jobsHandler.UpdateProgress))
	mux.Handle("PATCH /ai/jobs/{id}/complete", workerMW(jobsHandler.Complete))
	mux.Handle("POST /ai/jobs/{id}/logs", workerMW(jobsHandler.IngestLogs))
	mux.Handle("POST /ai/jobs/{id}/files", workerMW(jobsHandler.RegisterFile))

	// Start heartbeat monitor and retention job.
	go monitor.Run(ctx, pool)
	go retention.Run(ctx, pool)

	srv := &http.Server{
		Addr:    cfg.ListenAddr,
		Handler: withAdminCORS(mux),
	}

	go func() {
		log.Printf("api listening on %s", cfg.ListenAddr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("server: %v", err)
		}
	}()

	<-ctx.Done()
	log.Println("shutting down...")

	shutCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutCtx); err != nil {
		log.Printf("shutdown error: %v", err)
	}
}
