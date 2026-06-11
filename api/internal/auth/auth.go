package auth

import (
	"context"
	"net/http"

	"github.com/jackc/pgx/v5/pgxpool"
)

type Role int

const (
	RoleApp Role = iota
	RoleWorker
	RoleAdmin
)

type contextKey string

const (
	ctxRole      contextKey = "role"
	ctxAppID     contextKey = "app_id"
	ctxWorkerID  contextKey = "worker_id"
)

func GetRole(r *http.Request) Role {
	v, _ := r.Context().Value(ctxRole).(Role)
	return v
}

func GetAppID(r *http.Request) string {
	v, _ := r.Context().Value(ctxAppID).(string)
	return v
}

func GetWorkerID(r *http.Request) string {
	v, _ := r.Context().Value(ctxWorkerID).(string)
	return v
}

// RequireApp wraps a handler, only allowing app keys.
func RequireApp(pool *pgxpool.Pool, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("X-App-Key")
		if key == "" {
			http.Error(w, `{"error":"missing X-App-Key"}`, http.StatusUnauthorized)
			return
		}
		var appID string
		err := pool.QueryRow(r.Context(),
			`SELECT id FROM apps WHERE api_key = $1 AND active = true`, key,
		).Scan(&appID)
		if err != nil {
			http.Error(w, `{"error":"invalid or revoked key"}`, http.StatusUnauthorized)
			return
		}
		ctx := context.WithValue(r.Context(), ctxRole, RoleApp)
		ctx = context.WithValue(ctx, ctxAppID, appID)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// RequireWorker wraps a handler, only allowing worker keys.
func RequireWorker(pool *pgxpool.Pool, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("X-Worker-Key")
		if key == "" {
			http.Error(w, `{"error":"missing X-Worker-Key"}`, http.StatusUnauthorized)
			return
		}
		var workerID string
		err := pool.QueryRow(r.Context(),
			`SELECT id FROM workers WHERE api_key = $1`, key,
		).Scan(&workerID)
		if err != nil {
			http.Error(w, `{"error":"invalid worker key"}`, http.StatusUnauthorized)
			return
		}
		ctx := context.WithValue(r.Context(), ctxRole, RoleWorker)
		ctx = context.WithValue(ctx, ctxWorkerID, workerID)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// RequireAdmin wraps a handler, only allowing the admin key.
func RequireAdmin(adminKey string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("X-Admin-Key")
		if key == "" || key != adminKey {
			http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
			return
		}
		ctx := context.WithValue(r.Context(), ctxRole, RoleAdmin)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}
