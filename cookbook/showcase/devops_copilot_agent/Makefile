# Makefile
.PHONY: help build up down logs cli dashboard prom

help:
	@echo "make build     → build images"
	@echo "make up        → start full stack"
	@echo "make down      → stop all"
	@echo "make logs      → follow copilot logs"
	@echo "make cli       → run DevOps Copilot CLI"
	@echo "make dashboard → open Grafana"
	@echo "make prom      → open Prometheus[](http://localhost:9090)"

build:
	docker compose build

up:
	@mkdir -p logs
	@docker compose up -d
	@echo "Stack started!"
	@echo "  → Prometheus: http://localhost:9090"
	@echo "  → Grafana:    http://localhost:3000"
	@echo "  → Logs:       ./logs/"
	@echo "  → Copilot metrics: copilot:9091"

down:
	docker compose down

logs:
	docker compose logs -f copilot

cli:
	@echo "Starting OmniDevOpsCopilot CLI..."
	@mkdir -p logs
	@docker compose up -d
	@sleep 5
	@docker compose exec -t copilot python devops_copilot_agent.py

dashboard:
	@open http://localhost:3000 2>/dev/null || \
	 xdg-open http://localhost:3000 2>/dev/null || \
	 echo "Open Grafana: http://localhost:3000"

prom:
	@open http://localhost:9090 2>/dev/null || \
	 xdg-open http://localhost:9090 2>/dev/null || \
	 echo "Open Prometheus: http://localhost:9090"