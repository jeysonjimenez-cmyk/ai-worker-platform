package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

var (
	baseURL    = flag.String("api", "http://localhost:8080", "API base URL")
	workerID   = flag.String("id", "", "Worker ID (default: random)")
	services   = flag.String("services", "transcription,llm_chat", "Comma-separated services")
	adminKey   = flag.String("admin-key", os.Getenv("ADMIN_API_KEY"), "Admin API key (for register)")
	workerKey  = flag.String("worker-key", "", "Worker API key")
	waitSec    = flag.Int("wait", 30, "Long-poll timeout seconds")
	failAlways = flag.Bool("fail-always", false, "Always report error on complete")
	stopHB     = flag.Bool("stop-heartbeat", false, "Stop sending heartbeats after first claim")
	sleepSec   = flag.Int("sleep", 2, "Seconds to sleep before completing a job")
)

type jobResponse struct {
	ID      string `json:"id"`
	Service string `json:"service"`
	Status  string `json:"status"`
}

func do(method, url string, key, keyHeader string, body any) (*http.Response, error) {
	var buf bytes.Buffer
	if body != nil {
		json.NewEncoder(&buf).Encode(body)
	}
	req, err := http.NewRequest(method, url, &buf)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set(keyHeader, key)
	return http.DefaultClient.Do(req)
}

func main() {
	flag.Parse()

	if *workerID == "" {
		*workerID = fmt.Sprintf("sim-worker-%d", rand.Intn(100000))
	}
	if *workerKey == "" {
		*workerKey = fmt.Sprintf("wk-%s", *workerID)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Register.
	log.Printf("[%s] registering...", *workerID)
	svcList := splitServices(*services)
	caps := map[string]any{
		"services":    svcList,
		"cuda":        false,
		"vram_total_mb": 0,
		"max_concurrency": 1,
	}
	reg := map[string]any{
		"id": *workerID, "hostname": "sim-host",
		"capabilities": caps, "api_key": *workerKey,
		"gpu_id": "sim-host/gpu-0",
	}
	resp, err := do("POST", *baseURL+"/workers/register", *adminKey, "X-Admin-Key", reg)
	if err != nil || resp.StatusCode != http.StatusOK {
		log.Fatalf("register failed: %v status=%v", err, statusCode(resp))
	}
	resp.Body.Close()
	log.Printf("[%s] registered", *workerID)

	// Heartbeat loop.
	stopHBCh := make(chan struct{})
	go func() {
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-stopHBCh:
				log.Printf("[%s] heartbeat stopped", *workerID)
				return
			case <-ticker.C:
				resp, err := do("POST", *baseURL+"/workers/"+*workerID+"/heartbeat",
					*workerKey, "X-Worker-Key", nil)
				if err == nil {
					resp.Body.Close()
				}
			}
		}
	}()

	// Claim loop.
	for {
		if ctx.Err() != nil {
			break
		}
		url := fmt.Sprintf("%s/workers/%s/claim?wait=%d", *baseURL, *workerID, *waitSec)
		resp, err := do("POST", url, *workerKey, "X-Worker-Key", nil)
		if err != nil {
			log.Printf("[%s] claim error: %v", *workerID, err)
			time.Sleep(5 * time.Second)
			continue
		}

		if resp.StatusCode == http.StatusNoContent {
			resp.Body.Close()
			continue
		}
		if resp.StatusCode != http.StatusOK {
			resp.Body.Close()
			log.Printf("[%s] claim status %d", *workerID, resp.StatusCode)
			time.Sleep(2 * time.Second)
			continue
		}

		var job jobResponse
		json.NewDecoder(resp.Body).Decode(&job)
		resp.Body.Close()

		log.Printf("[%s] claimed job %s (service=%s)", *workerID, job.ID, job.Service)

		if *stopHB {
			close(stopHBCh)
			stopHB = boolPtr(false)
		}

		// Simulate work.
		time.Sleep(time.Duration(*sleepSec) * time.Second)

		// Report progress.
		progressURL := *baseURL + "/ai/jobs/" + job.ID + "/progress"
		pResp, _ := do("PATCH", progressURL, *workerKey, "X-Worker-Key", map[string]any{"progress": 50})
		if pResp != nil {
			pResp.Body.Close()
		}

		time.Sleep(time.Duration(*sleepSec) * time.Second)

		// Complete.
		completeURL := *baseURL + "/ai/jobs/" + job.ID + "/complete"
		var payload map[string]any
		if *failAlways {
			payload = map[string]any{"error": "simulated failure"}
		} else {
			payload = map[string]any{"result": map[string]string{"output": "simulated result"}}
		}
		cResp, err := do("PATCH", completeURL, *workerKey, "X-Worker-Key", payload)
		if err != nil {
			log.Printf("[%s] complete error: %v", *workerID, err)
			continue
		}
		log.Printf("[%s] job %s complete (status=%d)", *workerID, job.ID, cResp.StatusCode)
		cResp.Body.Close()
	}
	log.Printf("[%s] shutting down", *workerID)
}

func statusCode(resp *http.Response) int {
	if resp == nil {
		return 0
	}
	return resp.StatusCode
}

func boolPtr(b bool) *bool { return &b }

func splitServices(s string) []string {
	var out []string
	cur := ""
	for _, c := range s {
		if c == ',' {
			if cur != "" {
				out = append(out, cur)
			}
			cur = ""
		} else {
			cur += string(c)
		}
	}
	if cur != "" {
		out = append(out, cur)
	}
	return out
}
