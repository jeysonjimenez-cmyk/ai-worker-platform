package webhook

import (
	"bytes"
	"encoding/json"
	"net/http"
	"time"

	"github.com/jeysonjimenez-cmyk/ai-worker-platform/internal/ssrf"
)

type Dispatcher struct {
	client *http.Client
}

func New() *Dispatcher {
	return &Dispatcher{
		client: &http.Client{Timeout: 10 * time.Second},
	}
}

type payload struct {
	JobID  string          `json:"job_id"`
	Status string          `json:"status"`
	Result json.RawMessage `json:"result,omitempty"`
}

// Dispatch sends a webhook notification in a background goroutine.
// It re-validates the URL for SSRF before connecting, and retries up to 3 times.
func (d *Dispatcher) Dispatch(jobID, url, status string, result json.RawMessage) {
	go func() {
		// Re-validate SSRF at send time (DNS may have changed since creation).
		if err := ssrf.Validate(url); err != nil {
			return
		}
		body, _ := json.Marshal(payload{JobID: jobID, Status: status, Result: result})
		for attempt := range 3 {
			if attempt > 0 {
				time.Sleep(time.Duration(5*(1<<attempt)) * time.Second) // 10s, 20s
			}
			resp, err := d.client.Post(url, "application/json", bytes.NewReader(body))
			if err == nil {
				resp.Body.Close()
				return
			}
		}
	}()
}
