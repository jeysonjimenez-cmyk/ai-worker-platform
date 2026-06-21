package jobs

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/auth"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/ssrf"
	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/webhook"
)

// serviceRequirements maps service → default min_vram_mb.
var serviceRequirements = map[string]int{
	"transcription":       10000,
	"translation":         5300,
	"llm_chat":            5300,
	"tts":                 14720, // T7.6: calibrated from Higgs v3 real peak on ialab RTX 4070 Ti SUPER (idle 14472, synthesis peak 14720)
	"image_generation":    15000,
	"video_generation":    14000,
	"lipsync":             13000,
	"image_compose":       11000,
	"image_understanding": 4000,
	"embeddings":          2000,
}

// serviceMaxDuration maps service → max execution time before the monitor marks the job failed.
// Distinct from the heartbeat timeout (90s): this caps total run time, not idle time.
var serviceMaxDuration = map[string]time.Duration{
	"transcription":       4 * time.Hour,
	"translation":         2 * time.Hour,
	"llm_chat":            30 * time.Minute,
	"tts":                 15 * time.Minute,
	"image_generation":    10 * time.Minute,
	"video_generation":    2 * time.Hour,
	"lipsync":             1 * time.Hour,
	"image_compose":       15 * time.Minute,
	"image_understanding": 5 * time.Minute,
	"embeddings":          5 * time.Minute,
}

// T4.9/T6.6/T7.6: allow overriding VRAM requirements via env var so calibrated values
// can be adjusted without a code change after measuring on real hardware.
func init() {
	if s := os.Getenv("MIN_VRAM_TRANSCRIPTION_MB"); s != "" {
		if v, err := strconv.Atoi(s); err == nil && v > 0 {
			serviceRequirements["transcription"] = v
		}
	}
	if s := os.Getenv("MIN_VRAM_TRANSLATION_MB"); s != "" {
		if v, err := strconv.Atoi(s); err == nil && v > 0 {
			serviceRequirements["translation"] = v
		}
	}
	if s := os.Getenv("MIN_VRAM_LLM_CHAT_MB"); s != "" {
		if v, err := strconv.Atoi(s); err == nil && v > 0 {
			serviceRequirements["llm_chat"] = v
		}
	}
	if s := os.Getenv("MIN_VRAM_TTS_MB"); s != "" {
		if v, err := strconv.Atoi(s); err == nil && v > 0 {
			serviceRequirements["tts"] = v
		}
	}
}

// ServiceMaxDurations returns the per-service max execution duration map, for use by the monitor.
func ServiceMaxDurations() map[string]time.Duration {
	return serviceMaxDuration
}

var priorityMap = map[string]int{
	"high":   1,
	"normal": 5,
	"low":    10,
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

type Handler struct {
	pool          *pgxpool.Pool
	dispatcher    *webhook.Dispatcher
	fileServerURL string
}

func NewHandler(pool *pgxpool.Pool, dispatcher *webhook.Dispatcher, fileServerURL string) *Handler {
	return &Handler{pool: pool, dispatcher: dispatcher, fileServerURL: fileServerURL}
}

type createJobRequest struct {
	Service      string          `json:"service"`
	App          string          `json:"app"`
	Priority     string          `json:"priority"`
	Payload      json.RawMessage `json:"payload"`
	Requirements json.RawMessage `json:"requirements"`
	Routing      json.RawMessage `json:"routing"`
	WebhookURL   string          `json:"webhook_url"`
	WorkflowID   string          `json:"workflow_id"`
	MaxRetries   *int            `json:"max_retries"`
}

func (h *Handler) Create(w http.ResponseWriter, r *http.Request) {
	appID := auth.GetAppID(r)

	var req createJobRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid json: "+err.Error())
		return
	}
	if req.Service == "" {
		writeError(w, http.StatusUnprocessableEntity, "service is required")
		return
	}

	// SSRF validation on webhook_url at creation time.
	if req.WebhookURL != "" {
		if err := ssrf.ValidateStatic(req.WebhookURL); err != nil {
			writeError(w, http.StatusUnprocessableEntity, err.Error())
			return
		}
	}

	priority := priorityMap[req.Priority]
	if priority == 0 {
		priority = 5
	}

	// Infer requirements if not provided.
	requirements := req.Requirements
	if len(requirements) == 0 || string(requirements) == "null" {
		if vram, ok := serviceRequirements[req.Service]; ok {
			r, _ := json.Marshal(map[string]int{"min_vram_mb": vram})
			requirements = r
		}
	}

	// Default routing.
	routing := req.Routing
	if len(routing) == 0 || string(routing) == "null" {
		routing, _ = json.Marshal(map[string]any{"prefer": "local", "allow_external": false})
	}

	maxRetries := 3
	if req.MaxRetries != nil {
		maxRetries = *req.MaxRetries
	}

	var webhookURL *string
	if req.WebhookURL != "" {
		webhookURL = &req.WebhookURL
	}
	var workflowID *string
	if req.WorkflowID != "" {
		workflowID = &req.WorkflowID
	}

	appName := req.App
	if appName == "" {
		appName = appID
	}

	job, err := Create(r.Context(), h.pool, CreateParams{
		App:          appName,
		Service:      req.Service,
		Priority:     priority,
		Payload:      req.Payload,
		Requirements: requirements,
		Routing:      routing,
		MaxRetries:   maxRetries,
		WebhookURL:   webhookURL,
		WorkflowID:   workflowID,
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}

	// Notify waiting workers.
	h.pool.Exec(r.Context(), "SELECT pg_notify('jobs_channel', $1)", job.ID)

	writeJSON(w, http.StatusCreated, job)
}

func (h *Handler) GetByID(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	job, err := Get(r.Context(), h.pool, id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if job == nil {
		writeError(w, http.StatusNotFound, "job not found")
		return
	}
	writeJSON(w, http.StatusOK, job)
}

type progressRequest struct {
	Progress int     `json:"progress"`
	Message  *string `json:"message"`
	Level    *string `json:"level"`
}

func (h *Handler) UpdateProgress(w http.ResponseWriter, r *http.Request) {
	workerID := auth.GetWorkerID(r)
	jobID := r.PathValue("id")

	var req progressRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid json")
		return
	}

	owned, err := UpdateProgress(r.Context(), h.pool, UpdateProgressParams{
		JobID:    jobID,
		WorkerID: workerID,
		Progress: req.Progress,
		LogMsg:   req.Message,
		LogLevel: req.Level,
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if !owned {
		writeError(w, http.StatusConflict, "job not owned by this worker")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

type completeRequest struct {
	Result  json.RawMessage `json:"result"`
	Error   string          `json:"error"`
	CostUSD *float64        `json:"cost_usd"`
}

func (h *Handler) Complete(w http.ResponseWriter, r *http.Request) {
	workerID := auth.GetWorkerID(r)
	jobID := r.PathValue("id")

	var req completeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid json")
		return
	}

	var errMsg *string
	if req.Error != "" {
		errMsg = &req.Error
	}

	job, owned, err := Complete(r.Context(), h.pool, CompleteParams{
		JobID:    jobID,
		WorkerID: workerID,
		Result:   req.Result,
		CostUSD:  req.CostUSD,
		ErrMsg:   errMsg,
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if !owned {
		writeError(w, http.StatusConflict, "job not owned by this worker")
		return
	}

	// Fire webhook asynchronously for terminal states.
	if job.Status == "done" || job.Status == "error" {
		if job.WebhookURL != nil {
			h.dispatcher.Dispatch(job.ID, *job.WebhookURL, job.Status, job.Result)
		}
	}

	writeJSON(w, http.StatusOK, job)
}

type ingestLogsRequest struct {
	Logs []struct {
		Level   string `json:"level"`
		Message string `json:"message"`
	} `json:"logs"`
}

func (h *Handler) IngestLogs(w http.ResponseWriter, r *http.Request) {
	workerID := auth.GetWorkerID(r)
	jobID := r.PathValue("id")

	var req ingestLogsRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid json")
		return
	}
	if len(req.Logs) == 0 {
		writeError(w, http.StatusUnprocessableEntity, "logs must not be empty")
		return
	}

	entries := make([]LogEntry, len(req.Logs))
	for i, l := range req.Logs {
		if l.Message == "" {
			writeError(w, http.StatusUnprocessableEntity, fmt.Sprintf("logs[%d]: message is required", i))
			return
		}
		entries[i] = LogEntry{Level: l.Level, Message: l.Message}
	}

	owned, err := IngestLogs(r.Context(), h.pool, jobID, workerID, entries)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if !owned {
		writeError(w, http.StatusConflict, "job not owned by this worker")
		return
	}
	writeJSON(w, http.StatusCreated, map[string]int{"inserted": len(entries)})
}

type registerFileRequest struct {
	Filename  string `json:"filename"`
	Path      string `json:"path"`
	SizeBytes *int64 `json:"size_bytes"`
}

func (h *Handler) RegisterFile(w http.ResponseWriter, r *http.Request) {
	workerID := auth.GetWorkerID(r)
	jobID := r.PathValue("id")

	var req registerFileRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid json")
		return
	}
	if req.Filename == "" {
		writeError(w, http.StatusUnprocessableEntity, "filename is required")
		return
	}
	if req.Path == "" {
		writeError(w, http.StatusUnprocessableEntity, "path is required")
		return
	}

	owned, err := RegisterFile(r.Context(), h.pool, RegisterFileParams{
		JobID:     jobID,
		WorkerID:  workerID,
		Filename:  req.Filename,
		Path:      req.Path,
		SizeBytes: req.SizeBytes,
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if !owned {
		writeError(w, http.StatusConflict, "job not owned by this worker")
		return
	}
	writeJSON(w, http.StatusCreated, map[string]string{"status": "ok"})
}

// GetFile proxies GET /ai/jobs/{id}/files/{filename} to the ialab file server via Tailscale.
// Streams the response without buffering in memory.
// Returns 503 when the file server is unreachable.
func (h *Handler) GetFile(w http.ResponseWriter, r *http.Request) {
	appID := auth.GetAppID(r)
	jobID := r.PathValue("id")
	filename := r.PathValue("filename")

	// Defense-in-depth: reject suspicious filenames (router already blocks slashes via single-segment wildcard).
	if filename == "" || strings.Contains(filename, "..") || strings.ContainsAny(filename, "/\\") {
		writeError(w, http.StatusBadRequest, "invalid filename")
		return
	}

	// Verify the job belongs to the requesting app.
	owner, err := GetJobApp(r.Context(), h.pool, jobID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if owner == "" || owner != appID {
		writeError(w, http.StatusNotFound, "job not found")
		return
	}

	if h.fileServerURL == "" {
		writeError(w, http.StatusServiceUnavailable, "file server not configured")
		return
	}

	targetURL := h.fileServerURL + "/files/" + jobID + "/" + filename
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, targetURL, nil)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to build upstream request")
		return
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "file server unavailable")
		return
	}
	defer resp.Body.Close()

	if ct := resp.Header.Get("Content-Type"); ct != "" {
		w.Header().Set("Content-Type", ct)
	}
	if cl := resp.Header.Get("Content-Length"); cl != "" {
		w.Header().Set("Content-Length", cl)
	}
	w.WriteHeader(resp.StatusCode)
	io.Copy(w, resp.Body) //nolint:errcheck
}

func (h *Handler) Cancel(w http.ResponseWriter, r *http.Request) {
	appID := auth.GetAppID(r)
	jobID := r.PathValue("id")

	finalStatus, err := Cancel(r.Context(), h.pool, CancelParams{
		JobID: jobID,
		AppID: appID,
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if finalStatus == "" {
		writeError(w, http.StatusNotFound, "job not found or not owned by this app")
		return
	}
	if finalStatus == "done" || finalStatus == "error" {
		writeError(w, http.StatusConflict, "job already in terminal state: "+finalStatus)
		return
	}

	// Fire webhook for cancelled state.
	job, _ := Get(r.Context(), h.pool, jobID)
	if job != nil && job.WebhookURL != nil {
		h.dispatcher.Dispatch(job.ID, *job.WebhookURL, "cancelled", nil)
	}

	writeJSON(w, http.StatusOK, map[string]string{"status": "cancelled"})
}
