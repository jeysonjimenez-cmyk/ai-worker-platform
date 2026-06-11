package jobs

import "time"

// shouldRetry reports whether a failed job qualifies for another attempt.
func shouldRetry(retryCount, maxRetries int) bool {
	return retryCount < maxRetries
}

// nextRetryDelay returns the backoff duration before the next attempt.
// Sequence: 30s → 60s → 120s (doubles each time, base 30s).
func nextRetryDelay(retryCount int) time.Duration {
	return time.Duration(30*(1<<retryCount)) * time.Second
}
