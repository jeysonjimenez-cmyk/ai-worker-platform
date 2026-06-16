package admin

import (
	"encoding/json"
	"net/http"

	"github.com/jackc/pgx/v5/pgxpool"
)

type Handler struct {
	pool *pgxpool.Pool
}

func NewHandler(pool *pgxpool.Pool) *Handler {
	return &Handler{pool: pool}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

func (h *Handler) ListWorkers(w http.ResponseWriter, r *http.Request) {
	ws, err := ListWorkers(r.Context(), h.pool)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if ws == nil {
		ws = []WorkerSummary{}
	}
	writeJSON(w, http.StatusOK, ws)
}

func (h *Handler) ListJobs(w http.ResponseWriter, r *http.Request) {
	status := r.URL.Query().Get("status")
	js, err := ListJobs(r.Context(), h.pool, status)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if js == nil {
		js = []JobSummary{}
	}
	writeJSON(w, http.StatusOK, js)
}

func (h *Handler) GetJobDetail(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	j, err := GetJobDetail(r.Context(), h.pool, id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if j == nil {
		writeError(w, http.StatusNotFound, "job not found")
		return
	}
	writeJSON(w, http.StatusOK, j)
}

func (h *Handler) CancelJob(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	status, err := AdminCancelJob(r.Context(), h.pool, id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if status == "" {
		writeError(w, http.StatusNotFound, "job not found")
		return
	}
	if status == "done" || status == "error" {
		writeError(w, http.StatusConflict, "job already in terminal state: "+status)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "cancelled"})
}

func (h *Handler) RetryJob(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	status, err := AdminRetryJob(r.Context(), h.pool, id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if status == "" {
		writeError(w, http.StatusNotFound, "job not found")
		return
	}
	if status != "pending" {
		writeError(w, http.StatusConflict, "job is not in error state: "+status)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "pending"})
}
