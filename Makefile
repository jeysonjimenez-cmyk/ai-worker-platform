-include .env.deploy

VPS_USER   ?= ubuntu
VPS_HOST   ?= vps-15a6511a
VPS_DIR    ?= /home/ubuntu/ai-worker-platform
MIGRATE_BIN ?= migrate

.DEFAULT_GOAL := help

.PHONY: help build test lint deploy migrate-up migrate-down verify-infra

## help: list all targets with descriptions
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## //' | column -t -s ':'

## build: compile api/ (Go) and validate workers/ syntax (Python)
build:
	cd api && go build ./...
	cd workers && uv run ruff check --select E,F --quiet .

## test: run Go tests and Python tests
test:
	cd api && go test ./...
	cd workers && uv run python -m pytest --tb=short -q || true

## lint: lint Go (vet) and Python (ruff)
lint:
	cd api && go vet ./...
	cd workers && uv run ruff check .

## deploy: sync repo to VPS, restart compose, apply pending migrations
deploy:
	@echo "→ syncing to $(VPS_USER)@$(VPS_HOST):$(VPS_DIR)"
	rsync -az --delete \
		--exclude '.git' \
		--exclude '.env' \
		--exclude 'dashboard/node_modules' \
		--exclude 'workers/.venv' \
		. $(VPS_USER)@$(VPS_HOST):$(VPS_DIR)
	ssh $(VPS_USER)@$(VPS_HOST) "cd $(VPS_DIR)/deploy/vps && docker compose pull --quiet && docker compose up -d"
	$(MAKE) migrate-up VPS_DEPLOY=1

## migrate-up: apply all pending migrations
migrate-up:
ifdef VPS_DEPLOY
	ssh $(VPS_USER)@$(VPS_HOST) \
		"set -a && source $(VPS_DIR)/deploy/vps/.env && set +a && $(MIGRATE_BIN) -path $(VPS_DIR)/migrations -database \"\$$DATABASE_URL\" up"
else
	@echo "[pending] run against VPS: make migrate-up VPS_DEPLOY=1"
	@echo "  or: migrate -path ./migrations -database \$$DATABASE_URL up"
endif

## migrate-down: revert last migration
migrate-down:
ifdef VPS_DEPLOY
	ssh $(VPS_USER)@$(VPS_HOST) \
		"set -a && source $(VPS_DIR)/deploy/vps/.env && set +a && $(MIGRATE_BIN) -path $(VPS_DIR)/migrations -database \"\$$DATABASE_URL\" down 1"
else
	@echo "[pending] run against VPS: make migrate-down VPS_DEPLOY=1"
	@echo "  or: migrate -path ./migrations -database \$$DATABASE_URL down 1"
endif

## verify-infra: run full infra health check
verify-infra:
	@bash deploy/verify-infra.sh
