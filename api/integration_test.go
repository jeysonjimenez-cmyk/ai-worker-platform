package main_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	tc "github.com/testcontainers/testcontainers-go"
	tcpg "github.com/testcontainers/testcontainers-go/modules/postgres"
	"github.com/testcontainers/testcontainers-go/wait"

	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/auth"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/jobs"
	workersh "github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/workers"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/webhook"
)

const adminKey = "test-admin-key"
const appKey = "test-app-key"
const workerKey1 = "wk-test-1"

func setupTestDB(t *testing.T) (*pgxpool.Pool, func()) {
	t.Helper()
	ctx := context.Background()

	pg, err := tcpg.Run(ctx, "postgres:17-alpine",
		tcpg.WithDatabase("testdb"),
		tcpg.WithUsername("test"),
		tcpg.WithPassword("test"),
		tc.WithWaitStrategy(
			wait.ForLog("database system is ready to accept connections").
				WithOccurrence(2).WithStartupTimeout(30*time.Second),
		),
	)
	if err != nil {
		t.Fatalf("start postgres: %v", err)
	}

	dsn, err := pg.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		t.Fatalf("dsn: %v", err)
	}

	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatalf("pool: %v", err)
	}

	applyMigrations(t, pool)
	seedTestData(t, pool)

	return pool, func() {
		pool.Close()
		pg.Terminate(ctx)
	}
}

func applyMigrations(t *testing.T, pool *pgxpool.Pool) {
	t.Helper()
	ctx := context.Background()
	schema := `
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
`
	if _, err := pool.Exec(ctx, schema); err != nil {
		t.Fatalf("apply schema: %v", err)
	}
}

func seedTestData(t *testing.T, pool *pgxpool.Pool) {
	t.Helper()
	ctx := context.Background()
	_, err := pool.Exec(ctx,
		`INSERT INTO apps (name, api_key) VALUES ('test-app', $1)`, appKey,
	)
	if err != nil {
		t.Fatalf("seed app: %v", err)
	}
}

func buildServer(t *testing.T, pool *pgxpool.Pool) *httptest.Server {
	t.Helper()
	dispatcher := webhook.New()
	jobsHandler := jobs.NewHandler(pool, dispatcher)
	workersHandler := workersh.NewHandler(pool, 0) // no vram margin for tests

	mux := http.NewServeMux()
	appMW := func(h http.HandlerFunc) http.Handler { return auth.RequireApp(pool, h) }
	adminMW := func(h http.HandlerFunc) http.Handler { return auth.RequireAdmin(adminKey, h) }
	workerMW := func(h http.HandlerFunc) http.Handler { return auth.RequireWorker(pool, h) }

	mux.Handle("POST /ai/jobs", appMW(jobsHandler.Create))
	mux.Handle("GET /ai/jobs/{id}", appMW(jobsHandler.GetByID))
	mux.Handle("POST /ai/jobs/{id}/cancel", appMW(jobsHandler.Cancel))
	mux.Handle("POST /workers/register", adminMW(workersHandler.Register))
	mux.Handle("POST /workers/{id}/heartbeat", workerMW(workersHandler.Heartbeat))
	mux.Handle("POST /workers/{id}/claim", workerMW(workersHandler.Claim))
	mux.Handle("POST /workers/{id}/unload-model", workerMW(workersHandler.UnloadModel))
	mux.Handle("PATCH /ai/jobs/{id}/progress", workerMW(jobsHandler.UpdateProgress))
	mux.Handle("PATCH /ai/jobs/{id}/complete", workerMW(jobsHandler.Complete))

	return httptest.NewServer(mux)
}

func post(t *testing.T, srv *httptest.Server, path, keyHeader, key string, body any) *http.Response {
	t.Helper()
	b, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", srv.URL+path, strings.NewReader(string(b)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(keyHeader, key)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("POST %s: %v", path, err)
	}
	return resp
}

func patch(t *testing.T, srv *httptest.Server, path, keyHeader, key string, body any) *http.Response {
	t.Helper()
	b, _ := json.Marshal(body)
	req, _ := http.NewRequest("PATCH", srv.URL+path, strings.NewReader(string(b)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(keyHeader, key)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("PATCH %s: %v", path, err)
	}
	return resp
}

func get(t *testing.T, srv *httptest.Server, path, keyHeader, key string) *http.Response {
	t.Helper()
	req, _ := http.NewRequest("GET", srv.URL+path, nil)
	req.Header.Set(keyHeader, key)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("GET %s: %v", path, err)
	}
	return resp
}

func decodeJob(t *testing.T, resp *http.Response) map[string]any {
	t.Helper()
	var m map[string]any
	json.NewDecoder(resp.Body).Decode(&m)
	resp.Body.Close()
	return m
}

// ─── T1.3 auth tests ───

func TestAuth_MissingKey(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	req, _ := http.NewRequest("POST", srv.URL+"/ai/jobs", strings.NewReader(`{"service":"llm_chat"}`))
	req.Header.Set("Content-Type", "application/json")
	resp, _ := http.DefaultClient.Do(req)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("expected 401, got %d", resp.StatusCode)
	}
}

func TestAuth_WrongRoleAppOnWorkerEndpoint(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	resp := post(t, srv, "/workers/register", "X-App-Key", appKey, map[string]any{
		"id": "w1", "hostname": "h", "api_key": "k",
	})
	if resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("expected 401, got %d", resp.StatusCode)
	}
}

// ─── T1.4 job creation tests ───

func TestCreateJob_MissingService(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	resp := post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{"priority": "normal"})
	if resp.StatusCode != http.StatusUnprocessableEntity {
		t.Errorf("expected 422, got %d", resp.StatusCode)
	}
	resp.Body.Close()
}

func TestCreateJob_SSRFWebhook(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	cases := []struct {
		url    string
		expect int
	}{
		{"http://example.com/cb", http.StatusUnprocessableEntity},       // not https
		{"https://10.0.0.1/cb", http.StatusUnprocessableEntity},         // RFC 1918
		{"https://192.168.1.1/cb", http.StatusUnprocessableEntity},      // RFC 1918
		{"https://127.0.0.1/cb", http.StatusUnprocessableEntity},        // localhost
		{"https://100.64.0.1/cb", http.StatusUnprocessableEntity},       // Tailscale
	}
	for _, c := range cases {
		resp := post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{
			"service": "llm_chat", "webhook_url": c.url,
		})
		if resp.StatusCode != c.expect {
			t.Errorf("url=%s: expected %d, got %d", c.url, c.expect, resp.StatusCode)
		}
		resp.Body.Close()
	}
}

func TestCreateJob_PriorityMapping(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	cases := []struct{ priority string; expected float64 }{
		{"high", 1}, {"normal", 5}, {"low", 10},
	}
	for _, c := range cases {
		resp := post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{
			"service": "llm_chat", "priority": c.priority,
		})
		j := decodeJob(t, resp)
		if j["priority"] != c.expected {
			t.Errorf("priority=%s: expected %v, got %v", c.priority, c.expected, j["priority"])
		}
	}
}

func TestGetJob_NotFound(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	resp := get(t, srv, "/ai/jobs/nonexistent", "X-App-Key", appKey)
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("expected 404, got %d", resp.StatusCode)
	}
	resp.Body.Close()
}

// ─── T1.5 worker register & heartbeat ───

func registerWorker(t *testing.T, srv *httptest.Server, id, wKey string, caps map[string]any) {
	t.Helper()
	resp := post(t, srv, "/workers/register", "X-Admin-Key", adminKey, map[string]any{
		"id": id, "hostname": "testhost", "capabilities": caps, "api_key": wKey,
	})
	if resp.StatusCode != http.StatusOK {
		b, _ := json.Marshal(nil)
		json.NewDecoder(resp.Body).Decode(&b)
		t.Fatalf("register worker %s: status %d", id, resp.StatusCode)
	}
	resp.Body.Close()
}

func TestWorkerRegister_Idempotent(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	caps := map[string]any{"services": []string{"llm_chat"}, "cuda": false, "vram_total_mb": 0}
	registerWorker(t, srv, "w-idem", workerKey1, caps)
	registerWorker(t, srv, "w-idem", workerKey1, caps) // second register — should not error

	var count int
	pool.QueryRow(context.Background(), `SELECT count(*) FROM workers WHERE id = 'w-idem'`).Scan(&count)
	if count != 1 {
		t.Errorf("expected 1 worker row, got %d", count)
	}
}

func TestWorkerHeartbeat_NotFound(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	// Register with key first so auth passes, but heartbeat for unknown id.
	registerWorker(t, srv, "w-hb", workerKey1, map[string]any{"services": []string{"llm_chat"}, "cuda": false, "vram_total_mb": 0})

	resp := post(t, srv, "/workers/unknown-id/heartbeat", "X-Worker-Key", workerKey1, nil)
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("expected 404, got %d", resp.StatusCode)
	}
	resp.Body.Close()
}

// ─── T1.6 claim: SKIP LOCKED + VRAM reservation ───

func TestClaim_BasicFlow(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	caps := map[string]any{"services": []string{"llm_chat"}, "cuda": false, "vram_total_mb": 0}
	registerWorker(t, srv, "w-basic", workerKey1, caps)

	// Create job.
	cResp := post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{
		"service": "llm_chat", "requirements": map[string]int{"min_vram_mb": 0},
	})
	job := decodeJob(t, cResp)
	jobID := job["id"].(string)

	// Claim.
	claimResp := post(t, srv, "/workers/w-basic/claim", "X-Worker-Key", workerKey1, nil)
	if claimResp.StatusCode != http.StatusOK {
		t.Fatalf("claim: expected 200, got %d", claimResp.StatusCode)
	}
	claimed := decodeJob(t, claimResp)
	if claimed["id"] != jobID {
		t.Errorf("claimed wrong job: %v", claimed["id"])
	}
	if claimed["status"] != "running" {
		t.Errorf("expected running, got %v", claimed["status"])
	}
}

func TestClaim_VRAMNotEnough(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	// Register a worker with GPU (1000 MB total).
	caps := map[string]any{
		"services": []string{"transcription"},
		"cuda": true, "vram_total_mb": 1000,
	}
	registerWorker(t, srv, "w-vram", "wk-vram", caps)

	// Create a job that needs 2000 MB.
	post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{
		"service":      "transcription",
		"requirements": map[string]int{"min_vram_mb": 2000},
	}).Body.Close()

	// Claim should return 204 (no eligible job due to VRAM).
	claimResp := post(t, srv, "/workers/w-vram/claim", "X-Worker-Key", "wk-vram", nil)
	if claimResp.StatusCode != http.StatusNoContent {
		t.Errorf("expected 204 (no vram), got %d", claimResp.StatusCode)
	}
	claimResp.Body.Close()
}

func TestClaim_ServiceMismatch(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	caps := map[string]any{"services": []string{"tts"}, "cuda": false, "vram_total_mb": 0}
	registerWorker(t, srv, "w-svc", "wk-svc", caps)

	// Create an llm_chat job.
	post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{
		"service": "llm_chat",
	}).Body.Close()

	claimResp := post(t, srv, "/workers/w-svc/claim", "X-Worker-Key", "wk-svc", nil)
	if claimResp.StatusCode != http.StatusNoContent {
		t.Errorf("expected 204 (service mismatch), got %d", claimResp.StatusCode)
	}
	claimResp.Body.Close()
}

// ─── T1.8 VRAM release idempotency ───

func TestReleaseVRAM_DoubleRelease(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	ctx := context.Background()

	// Set up GPU with 10000 MB.
	pool.Exec(ctx, `INSERT INTO gpus (id, hostname, vram_total_mb) VALUES ('testhost/gpu-0','testhost',10000)`)
	pool.Exec(ctx, `INSERT INTO workers (id, hostname, status, capabilities, gpu_id, api_key)
		VALUES ('w-rel','testhost','online','{"services":["transcription"],"cuda":true,"vram_total_mb":10000}','testhost/gpu-0','wk-rel')`)
	pool.Exec(ctx, `UPDATE gpus SET vram_reserved_mb = 5000 WHERE id = 'testhost/gpu-0'`)

	// Insert a running job.
	var jobID string
	pool.QueryRow(ctx, `
		INSERT INTO jobs (app, service, requirements, status, worker_id)
		VALUES ('app','transcription','{"min_vram_mb":5000}','running','w-rel')
		RETURNING id`).Scan(&jobID)

	// Release once.
	tx1, _ := pool.Begin(ctx)
	jobs.ReleaseVRAMReservation(ctx, tx1, jobID)
	tx1.Commit(ctx)

	// Release twice — should be a no-op.
	tx2, _ := pool.Begin(ctx)
	jobs.ReleaseVRAMReservation(ctx, tx2, jobID)
	tx2.Commit(ctx)

	var reserved int
	pool.QueryRow(ctx, `SELECT vram_reserved_mb FROM gpus WHERE id = 'testhost/gpu-0'`).Scan(&reserved)
	if reserved != 0 {
		t.Errorf("double release: expected 0, got %d", reserved)
	}
}

// ─── T1.9 progress + complete fencing ───

func TestProgressFencing(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	// Set up two workers.
	caps := map[string]any{"services": []string{"llm_chat"}, "cuda": false, "vram_total_mb": 0}
	registerWorker(t, srv, "w-f1", workerKey1, caps)
	registerWorker(t, srv, "w-f2", "wk-f2", caps)

	// Create and claim job by w-f1.
	post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{"service": "llm_chat"}).Body.Close()
	claimResp := post(t, srv, "/workers/w-f1/claim", "X-Worker-Key", workerKey1, nil)
	job := decodeJob(t, claimResp)
	jobID := job["id"].(string)

	// w-f2 tries to update progress — should be 409.
	resp := patch(t, srv, "/ai/jobs/"+jobID+"/progress", "X-Worker-Key", "wk-f2", map[string]any{"progress": 50})
	if resp.StatusCode != http.StatusConflict {
		t.Errorf("expected 409 fencing, got %d", resp.StatusCode)
	}
	resp.Body.Close()
}

func TestCompleteFencing(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	caps := map[string]any{"services": []string{"llm_chat"}, "cuda": false, "vram_total_mb": 0}
	registerWorker(t, srv, "w-cf1", workerKey1, caps)
	registerWorker(t, srv, "w-cf2", "wk-cf2", caps)

	post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{"service": "llm_chat"}).Body.Close()
	claimResp := post(t, srv, "/workers/w-cf1/claim", "X-Worker-Key", workerKey1, nil)
	j := decodeJob(t, claimResp)
	jobID := j["id"].(string)

	// w-cf2 tries to complete — should be 409.
	resp := patch(t, srv, "/ai/jobs/"+jobID+"/complete", "X-Worker-Key", "wk-cf2",
		map[string]any{"result": map[string]string{"out": "x"}})
	if resp.StatusCode != http.StatusConflict {
		t.Errorf("expected 409 fencing on complete, got %d", resp.StatusCode)
	}
	resp.Body.Close()
}

// ─── T1.10 cancel ───

func TestCancelJob(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	// Cancel pending job.
	cResp := post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{"service": "llm_chat"})
	j := decodeJob(t, cResp)
	jobID := j["id"].(string)

	resp := post(t, srv, "/ai/jobs/"+jobID+"/cancel", "X-App-Key", appKey, nil)
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200 cancel, got %d", resp.StatusCode)
	}
	resp.Body.Close()

	// Cancel done job — should be 409.
	caps := map[string]any{"services": []string{"llm_chat"}, "cuda": false, "vram_total_mb": 0}
	registerWorker(t, srv, "w-can", "wk-can", caps)
	post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{"service": "llm_chat"}).Body.Close()
	claimResp := post(t, srv, "/workers/w-can/claim", "X-Worker-Key", "wk-can", nil)
	cj := decodeJob(t, claimResp)
	cjID := cj["id"].(string)

	patch(t, srv, "/ai/jobs/"+cjID+"/complete", "X-Worker-Key", "wk-can",
		map[string]any{"result": map[string]string{"out": "x"}}).Body.Close()

	resp2 := post(t, srv, "/ai/jobs/"+cjID+"/cancel", "X-App-Key", appKey, nil)
	if resp2.StatusCode != http.StatusConflict {
		t.Errorf("expected 409 on done-cancel, got %d", resp2.StatusCode)
	}
	resp2.Body.Close()
}

func TestCancelByWrongApp(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	// Create a second app.
	pool.Exec(context.Background(), `INSERT INTO apps (name, api_key) VALUES ('other-app', 'other-key')`)

	cResp := post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{"service": "llm_chat"})
	j := decodeJob(t, cResp)
	jobID := j["id"].(string)

	resp := post(t, srv, "/ai/jobs/"+jobID+"/cancel", "X-App-Key", "other-key", nil)
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("expected 404 (wrong app), got %d", resp.StatusCode)
	}
	resp.Body.Close()
}

// ─── T1.12 retry with backoff ───

func TestRetryBackoff(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	ctx := context.Background()

	// Create a job with max_retries=2.
	j, _ := jobs.Create(ctx, pool, jobs.CreateParams{
		App: "test-app", Service: "llm_chat",
		Priority: 5, MaxRetries: 2,
	})

	// Set up worker.
	pool.Exec(ctx, `INSERT INTO workers (id, hostname, status, capabilities, api_key)
		VALUES ('w-retry','h','online','{"services":["llm_chat"]}','wk-retry')`)

	// Fail 3 times (max_retries=2 → 2 retries, 3rd attempt is final error).
	for i := range 3 {
		// Claim manually.
		pool.Exec(ctx, `UPDATE jobs SET status='running', worker_id='w-retry', started_at=now(), vram_released=false WHERE id=$1`, j.ID)

		errMsg := "simulated error"
		result, owned, err := jobs.Complete(ctx, pool, jobs.CompleteParams{
			JobID: j.ID, WorkerID: "w-retry", ErrMsg: &errMsg,
		})
		if err != nil {
			t.Fatalf("complete attempt %d: %v", i+1, err)
		}
		if !owned {
			t.Fatalf("attempt %d: not owned", i+1)
		}

		if i < 2 {
			if result.Status != "pending" {
				t.Errorf("attempt %d: expected pending (retry), got %s", i+1, result.Status)
			}
			if result.RetryAfter == nil {
				t.Errorf("attempt %d: expected retry_after to be set", i+1)
			}
		} else {
			if result.Status != "error" {
				t.Errorf("attempt 3: expected error (exhausted), got %s", result.Status)
			}
		}
	}
}

func TestRetryMaxZero(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	ctx := context.Background()

	maxR := 0
	j, _ := jobs.Create(ctx, pool, jobs.CreateParams{
		App: "test-app", Service: "llm_chat", Priority: 5, MaxRetries: maxR,
	})
	pool.Exec(ctx, `INSERT INTO workers (id, hostname, status, capabilities, api_key)
		VALUES ('w-r0','h','online','{"services":["llm_chat"]}','wk-r0')`)
	pool.Exec(ctx, `UPDATE jobs SET status='running', worker_id='w-r0', started_at=now(), vram_released=false WHERE id=$1`, j.ID)

	errMsg := "fail"
	result, _, _ := jobs.Complete(ctx, pool, jobs.CompleteParams{
		JobID: j.ID, WorkerID: "w-r0", ErrMsg: &errMsg,
	})
	if result.Status != "error" {
		t.Errorf("max_retries=0: expected error, got %s", result.Status)
	}
}

// ─── T1.15 VRAM ledger race test ───

func TestVRAMLedgerRace(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	ctx := context.Background()
	srv := buildServer(t, pool)
	defer srv.Close()

	// One GPU with 10000 MB.
	pool.Exec(ctx, `INSERT INTO gpus (id, hostname, vram_total_mb) VALUES ('testhost/gpu-0','testhost',10000)`)

	const numWorkers = 10
	const jobVRAM = 3000 // 10 jobs × 3000 MB > 10000 MB total

	// Register workers with GPU capabilities.
	for i := range numWorkers {
		id := fmt.Sprintf("w-race-%d", i)
		key := fmt.Sprintf("wk-race-%d", i)
		pool.Exec(ctx, `
			INSERT INTO workers (id, hostname, status, capabilities, gpu_id, api_key)
			VALUES ($1,'testhost','online',
			  '{"services":["transcription"],"cuda":true,"vram_total_mb":10000}',
			  'testhost/gpu-0', $2)`,
			id, key,
		)
	}

	// Create 10 jobs each needing 3000 MB.
	for range numWorkers {
		post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{
			"service":      "transcription",
			"requirements": map[string]int{"min_vram_mb": jobVRAM},
		}).Body.Close()
	}

	// All workers try to claim concurrently.
	var claimed atomic.Int32
	var wg sync.WaitGroup
	for i := range numWorkers {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			resp := post(t, srv, fmt.Sprintf("/workers/w-race-%d/claim", idx),
				"X-Worker-Key", fmt.Sprintf("wk-race-%d", idx), nil)
			if resp.StatusCode == http.StatusOK {
				claimed.Add(1)
				resp.Body.Close()
			} else {
				resp.Body.Close()
			}
		}(i)
	}
	wg.Wait()

	// Check: vram_reserved_mb must not exceed vram_total_mb.
	var reserved, total int
	pool.QueryRow(ctx, `SELECT vram_reserved_mb, vram_total_mb FROM gpus WHERE id='testhost/gpu-0'`).Scan(&reserved, &total)
	if reserved > total {
		t.Errorf("VRAM OVER-RESERVED: reserved=%d total=%d claimed=%d",
			reserved, total, claimed.Load())
	}
	// With 10000 MB total and 3000 MB/job, exactly 3 jobs can fit.
	if int(claimed.Load()) > total/jobVRAM {
		t.Errorf("too many claims: %d claimed but max=%d", claimed.Load(), total/jobVRAM)
	}
	t.Logf("race test: %d workers, %d claimed, reserved=%d MB", numWorkers, claimed.Load(), reserved)
}

// ─── T1.11 heartbeat monitor ───

func TestHeartbeatTimeout(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	ctx := context.Background()
	srv := buildServer(t, pool)
	defer srv.Close()

	caps := map[string]any{"services": []string{"llm_chat"}, "cuda": false, "vram_total_mb": 0}
	registerWorker(t, srv, "w-to", "wk-to", caps)

	// Create and claim a job.
	post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{"service": "llm_chat"}).Body.Close()
	claimResp := post(t, srv, "/workers/w-to/claim", "X-Worker-Key", "wk-to", nil)
	j := decodeJob(t, claimResp)
	jobID := j["id"].(string)

	// Simulate heartbeat timeout by backdating last_heartbeat.
	pool.Exec(ctx, `UPDATE workers SET last_heartbeat = now() - '120s'::interval WHERE id = 'w-to'`)

	// Run timeout handler directly.
	if err := workersh.HandleWorkerTimeout(ctx, pool, "w-to"); err != nil {
		t.Fatalf("timeout handler: %v", err)
	}

	// Job should be back to pending.
	var status string
	pool.QueryRow(ctx, `SELECT status FROM jobs WHERE id=$1`, jobID).Scan(&status)
	if status != "pending" {
		t.Errorf("expected pending after timeout, got %s", status)
	}

	// Worker should be offline.
	var wStatus string
	pool.QueryRow(ctx, `SELECT status FROM workers WHERE id='w-to'`).Scan(&wStatus)
	if wStatus != "offline" {
		t.Errorf("expected worker offline, got %s", wStatus)
	}

	// Zombie complete should get 409.
	resp := patch(t, srv, "/ai/jobs/"+jobID+"/complete", "X-Worker-Key", "wk-to",
		map[string]any{"result": map[string]string{"out": "zombie"}})
	if resp.StatusCode != http.StatusConflict {
		t.Errorf("expected 409 for zombie complete, got %d", resp.StatusCode)
	}
	resp.Body.Close()
}

// ─── T1.14 end-to-end lifecycle ───

func TestEndToEndLifecycle(t *testing.T) {
	pool, cleanup := setupTestDB(t)
	defer cleanup()
	srv := buildServer(t, pool)
	defer srv.Close()

	caps := map[string]any{"services": []string{"llm_chat"}, "cuda": false, "vram_total_mb": 0}
	registerWorker(t, srv, "w-e2e", "wk-e2e", caps)

	// Create job.
	cResp := post(t, srv, "/ai/jobs", "X-App-Key", appKey, map[string]any{"service": "llm_chat"})
	if cResp.StatusCode != http.StatusCreated {
		t.Fatalf("create: expected 201, got %d", cResp.StatusCode)
	}
	j := decodeJob(t, cResp)
	jobID := j["id"].(string)
	if j["status"] != "pending" {
		t.Errorf("expected pending, got %v", j["status"])
	}

	// Claim.
	claimResp := post(t, srv, "/workers/w-e2e/claim", "X-Worker-Key", "wk-e2e", nil)
	claimed := decodeJob(t, claimResp)
	if claimed["status"] != "running" {
		t.Errorf("expected running, got %v", claimed["status"])
	}

	// Progress.
	pResp := patch(t, srv, "/ai/jobs/"+jobID+"/progress", "X-Worker-Key", "wk-e2e",
		map[string]any{"progress": 50, "message": "halfway"})
	if pResp.StatusCode != http.StatusOK {
		t.Errorf("progress: expected 200, got %d", pResp.StatusCode)
	}
	pResp.Body.Close()

	// GET — check progress.
	gResp := get(t, srv, "/ai/jobs/"+jobID, "X-App-Key", appKey)
	gj := decodeJob(t, gResp)
	if gj["progress"] != float64(50) {
		t.Errorf("expected progress=50, got %v", gj["progress"])
	}

	// Complete.
	compResp := patch(t, srv, "/ai/jobs/"+jobID+"/complete", "X-Worker-Key", "wk-e2e",
		map[string]any{"result": map[string]string{"output": "hello"}})
	if compResp.StatusCode != http.StatusOK {
		t.Fatalf("complete: expected 200, got %d", compResp.StatusCode)
	}
	compJ := decodeJob(t, compResp)
	if compJ["status"] != "done" {
		t.Errorf("expected done, got %v", compJ["status"])
	}

	// GET — final state.
	finalResp := get(t, srv, "/ai/jobs/"+jobID, "X-App-Key", appKey)
	fj := decodeJob(t, finalResp)
	if fj["status"] != "done" {
		t.Errorf("expected done, got %v", fj["status"])
	}
	if fj["result"] == nil {
		t.Error("expected result to be set")
	}
}
