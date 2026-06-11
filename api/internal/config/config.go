package config

import (
	"fmt"
	"os"
)

type Config struct {
	DatabaseURL  string
	ListenAddr   string
	AdminAPIKey  string
	VRAMMarginMB int
}

func Load() (*Config, error) {
	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		return nil, fmt.Errorf("DATABASE_URL is required")
	}
	adminKey := os.Getenv("ADMIN_API_KEY")
	if adminKey == "" {
		return nil, fmt.Errorf("ADMIN_API_KEY is required")
	}
	addr := os.Getenv("LISTEN_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	return &Config{
		DatabaseURL:  dbURL,
		ListenAddr:   addr,
		AdminAPIKey:  adminKey,
		VRAMMarginMB: 500,
	}, nil
}
