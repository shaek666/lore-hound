.PHONY: up down build logs shell migrate test check clean

# ── Lifecycle ──────────────────────────────────────────────────────────────

up:   ## Build images and start all services (detached)
	docker compose up -d

down: ## Stop services (data preserved in volumes)
	docker compose down

build: ## Force rebuild without cache
	docker compose build --no-cache

logs: ## Tail app logs
	docker compose logs -f app

shell: ## Open a shell in the app container
	docker compose exec app bash

# ── Django admin ───────────────────────────────────────────────────────────

migrate: ## Run pending database migrations
	docker compose exec app python manage.py migrate

test: ## Run tests inside the container
	docker compose exec app python -m pytest -v

check: ## Run Django system checks (production mode)
	docker compose exec app python manage.py check --deploy

createsuperuser: ## Create an admin user
	docker compose exec app python manage.py createsuperuser

seed: ## Seed sample data
	docker compose exec app python manage.py seed_data

# ── Local (host-python, not container) ─────────────────────────────────────

test-local: ## Run tests using host Python (requires uv sync)
	uv run python -m pytest -v

check-local: ## Run Django checks using host Python
	uv run python manage.py check --deploy

# ── Housekeeping ───────────────────────────────────────────────────────────

clean: ## Remove all local temp/ cache files
	rm -rf .pytest_cache .coverage* htmlcov .tmp/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
