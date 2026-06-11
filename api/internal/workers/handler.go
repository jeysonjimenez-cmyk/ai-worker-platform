package workers

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/auth"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/jobs"
)

type Handler struct {
	pool              *pgxpool.Pool
	vramMarginMB      int
	vramDriftMarginMB int
}

func NewHandler(pool *pgxpool.Pool, vramMarginMB, vramDriftMarginMB int) *Handler {
	return &Handler{pool: pool, vramMarginMB: vramMarginMB, vramDriftMarginMB: vramDriftMarginMB}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

type registerRequest struct {
	ID           string          `json:"id"`
	Hostname     string          `json:"hostname"`
	Capabilities json.RawMessage `json:"capabilities"`
	APIKey       string          `json:"api_key"`
	GPUID        *string         `json:"gpu_id,omitempty"`
}

func (h *Handler) Register(w http.ResponseWriter, r *http.Request) {
	var req registerRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid json: "+err.Error())
		return
	}
	if req.ID == "" || req.Hostname == "" || req.APIKey == "" {
		writeError(w, http.StatusUnprocessableEntity, "id, hostname and api_key are required")
		return
	}

	worker, err := Register(r.Context(), h.pool, RegisterParams{
		ID:           req.ID,
		Hostname:     req.Hostname,
		Capabilities: req.Capabilities,
		APIKey:       req.APIKey,
		GPUID:        req.GPUID,
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, worker)
}

func (h *Handler) Heartbeat(w http.ResponseWriter, r *http.Request) {
	workerID := r.PathValue("id")
	if err := Heartbeat(r.Context(), h.pool, workerID); err != nil {
		writeError(w, http.StatusNotFound, "worker not found")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// Claim handles POST /workers/{id}/claim with optional long-polling (?wait=N).
func (h *Handler) Claim(w http.ResponseWriter, r *http.Request) {
	workerID := auth.GetWorkerID(r)
	pathID := r.PathValue("id")
	if workerID != pathID {
		writeError(w, http.StatusForbidden, "worker id mismatch")
		return
	}

	waitSec := 0
	if s := r.URL.Query().Get("wait"); s != "" {
		waitSec, _ = strconv.Atoi(s)
	}

	job, err := Claim(r.Context(), h.pool, workerID, h.vramMarginMB)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if job != nil {
		writeJSON(w, http.StatusOK, job)
		return
	}
	if waitSec <= 0 {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	// Long-polling: listen for notifications and retry claim.
	job, err = h.claimWithWait(r.Context(), workerID, waitSec)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if job == nil {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	writeJSON(w, http.StatusOK, job)
}

func (h *Handler) claimWithWait(ctx context.Context, workerID string, waitSec int) (*jobs.Job, error) {
	deadline := time.Now().Add(time.Duration(waitSec) * time.Second)

	// Acquire a dedicated connection for LISTEN.
	conn, err := h.pool.Acquire(ctx)
	if err != nil {
		return nil, err
	}
	defer conn.Release()

	if _, err := conn.Exec(ctx, "LISTEN jobs_channel"); err != nil {
		return nil, err
	}

	for time.Now().Before(deadline) {
		remaining := time.Until(deadline)
		waitCtx, cancel := context.WithTimeout(ctx, remaining)
		_, err := conn.Conn().WaitForNotification(waitCtx)
		cancel()
		if err != nil {
			if ctx.Err() != nil {
				return nil, nil
			}
			// Timeout or connection issue.
			break
		}
		// Got notification — attempt claim.
		job, err := Claim(ctx, h.pool, workerID, h.vramMarginMB)
		if err != nil {
			return nil, err
		}
		if job != nil {
			return job, nil
		}
	}
	return nil, nil
}

type metricsRequest struct {
	Samples []MetricSample `json:"samples"`
}

func (h *Handler) IngestMetrics(w http.ResponseWriter, r *http.Request) {
	workerID := auth.GetWorkerID(r)
	pathID := r.PathValue("id")
	if workerID != pathID {
		writeError(w, http.StatusForbidden, "worker id mismatch")
		return
	}

	var req metricsRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid json: "+err.Error())
		return
	}
	for i, s := range req.Samples {
		if s.RecordedAt.IsZero() {
			writeError(w, http.StatusUnprocessableEntity, fmt.Sprintf("samples[%d]: recorded_at is required", i))
			return
		}
	}

	if err := IngestMetrics(r.Context(), h.pool, workerID, req.Samples, h.vramDriftMarginMB); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"inserted": len(req.Samples)})
}

func (h *Handler) UnloadModel(w http.ResponseWriter, r *http.Request) {
	workerID := auth.GetWorkerID(r)
	pathID := r.PathValue("id")
	if workerID != pathID {
		writeError(w, http.StatusForbidden, "worker id mismatch")
		return
	}
	if err := ReleaseModelVRAM(r.Context(), h.pool, workerID); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}
