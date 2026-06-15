package config

import (
	"fmt"
	"os"
	"strconv"
)

type Config struct {
	DatabaseURL       string
	ListenAddr        string
	AdminAPIKey       string
	VRAMMarginMB      int
	VRAMDriftMarginMB int
	FileServerURL     string
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
	driftMargin := 512
	if s := os.Getenv("VRAM_DRIFT_MARGIN_MB"); s != "" {
		if v, err := strconv.Atoi(s); err == nil {
			driftMargin = v
		}
	}
	// T4.9: allow adjusting VRAM margin after measuring real model overhead.
	vramMargin := 500
	if s := os.Getenv("VRAM_MARGIN_MB"); s != "" {
		if v, err := strconv.Atoi(s); err == nil && v >= 0 {
			vramMargin = v
		}
	}
	return &Config{
		DatabaseURL:       dbURL,
		ListenAddr:        addr,
		AdminAPIKey:       adminKey,
		VRAMMarginMB:      vramMargin,
		VRAMDriftMarginMB: driftMargin,
		FileServerURL:     os.Getenv("FILE_SERVER_URL"),
	}, nil
}
