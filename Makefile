-include .env.deploy

VPS_USER    ?= ubuntu
VPS_HOST    ?= vps-15a6511a
VPS_DIR     ?= /home/ubuntu/ai-worker-platform
MIGRATE_BIN ?= migrate

IALAB_USER  ?= ubuntu
IALAB_HOST  ?= 100.103.55.110
IALAB_DIR   ?= /home/ubuntu/ai-worker-platform
DISK_MIN_GB ?= 5
VPS_API_URL ?= http://100.106.192.45:8081

.DEFAULT_GOAL := help

.PHONY: help build test lint deploy migrate-up migrate-down verify-infra verify-environment workers-build workers-up workers-down workers-logs deploy-ialab verify-ialab

## help: list all targets with descriptions
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## //' | column -t -s ':'

## build: compile api/ (Go) and validate Python syntax (workers, agent)
build:
	cd api && go build ./...
	cd workers && uv run ruff check --select E,F --quiet .
	cd agent && uv run ruff check --select E,F --quiet .

## test: run Go tests and Python tests (workers, agent)
test:
	cd api && go test ./...
	cd workers && uv run python -m pytest --tb=short -q || true
	cd agent && uv run pytest tests/ --tb=short -q

## lint: lint Go (vet) and Python (ruff)
lint:
	cd api && go vet ./...
	cd workers && uv run ruff check .
	cd agent && uv run ruff check .

## deploy: sync repo to VPS, rebuild compose, apply pending migrations
deploy:
	@echo "→ syncing to $(VPS_USER)@$(VPS_HOST):$(VPS_DIR)"
	rsync -az --delete \
		--exclude '.git' \
		--exclude '.env' \
		--exclude 'dashboard/node_modules' \
		--exclude 'workers/.venv' \
		. $(VPS_USER)@$(VPS_HOST):$(VPS_DIR)
	ssh $(VPS_USER)@$(VPS_HOST) "bash $(VPS_DIR)/deploy/vps/deploy.sh --no-pull"

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

## verify-environment: pre-flight check before starting a phase (tooling, Tailscale, Docker, GPU, PostgreSQL)
verify-environment:
	@bash scripts/verify-environment.sh

## workers-build: build worker images locally (verifies Dockerfile + frozen deps)
workers-build:
	docker compose -f deploy/ialab/docker-compose.yml build

## workers-up: start workers in ialab (run from ialab)
workers-up:
	docker compose -f deploy/ialab/docker-compose.yml up -d

## workers-down: stop workers in ialab
workers-down:
	docker compose -f deploy/ialab/docker-compose.yml down

## workers-logs: tail worker logs (run from ialab)
workers-logs:
	docker compose -f deploy/ialab/docker-compose.yml logs -f

## deploy-ialab: sync repo to ialab, rebuild workers with --force-recreate, verify registration
deploy-ialab:
	@echo "→ syncing to $(IALAB_USER)@$(IALAB_HOST):$(IALAB_DIR)"
	rsync -az --delete \
		--exclude '.git' \
		--exclude '.env' \
		--exclude 'dashboard/node_modules' \
		--exclude 'workers/.venv' \
		. $(IALAB_USER)@$(IALAB_HOST):$(IALAB_DIR)
	ssh $(IALAB_USER)@$(IALAB_HOST) "bash $(IALAB_DIR)/deploy/ialab/deploy-workers.sh $(DISK_MIN_GB)"
	@ADMIN_API_KEY="$(ADMIN_API_KEY)" IALAB_USER="$(IALAB_USER)" IALAB_HOST="$(IALAB_HOST)" \
	  IALAB_DIR="$(IALAB_DIR)" VPS_API_URL="$(VPS_API_URL)" \
	  bash deploy/ialab/verify-workers.sh

## verify-ialab: verify worker registration on ialab without redeploying
verify-ialab:
	@ADMIN_API_KEY="$(ADMIN_API_KEY)" IALAB_USER="$(IALAB_USER)" IALAB_HOST="$(IALAB_HOST)" \
	  IALAB_DIR="$(IALAB_DIR)" VPS_API_URL="$(VPS_API_URL)" \
	  bash deploy/ialab/verify-workers.sh
