.PHONY: help ci lint format typecheck test security docker-build clean up down restart logs ps

# All targets call tools via this path instead of relying on `$PATH`, so `make ci`
# (and friends) work the same whether or not you've run `source venv/bin/activate`
# in the current shell.
VENV_BIN := venv/bin

# Env vars used by the `test` target — same placeholders as .github/workflows/ci.yml,
# so tests run the same way locally as they do in CI without needing a real .env.
export BOT_TOKEN ?= test_token_placeholder
export NVIDIA_API_KEY ?= test_key_placeholder
export WEBHOOK_HOST ?= https://example.com
export WEBHOOK_SECRET ?= test_webhook_secret
export POSTGRES_USER ?= test_user
export POSTGRES_PASSWORD ?= test_password
export POSTGRES_DB ?= test_db
export POSTGRES_HOST ?= localhost
export POSTGRES_PORT ?= 5432
export REDIS_HOST ?= localhost
export REDIS_PORT ?= 6379

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

check-venv:
	@test -x $(VENV_BIN)/python3 || (echo "❌ venv not found at ./$(VENV_BIN). Create it first: uv venv --python 3.14 venv && venv/bin/pip install -r requirements/base.txt -r requirements/dev.txt" && exit 1)

ci: lint typecheck test security docker-build ## Run every CI job locally, in the same order as GitHub Actions

lint: check-venv ## Run ruff lint + format check (mirrors the "lint" CI job)
	$(VENV_BIN)/ruff check .
	$(VENV_BIN)/ruff format --check .

format: check-venv ## Auto-fix lint issues and reformat code (not run in CI, but fixes what `lint` complains about)
	$(VENV_BIN)/ruff check --fix .
	$(VENV_BIN)/ruff format .

typecheck: check-venv ## Run mypy (mirrors the "typecheck" CI job)
	$(VENV_BIN)/mypy . --ignore-missing-imports

test: check-venv ## Run pytest with coverage (mirrors the "test" CI job). Needs Postgres+Redis reachable via the vars above.
	$(VENV_BIN)/python3 -m pytest -v --tb=short --cov --cov-report=term-missing --cov-report=xml

security: check-venv ## Run pip-audit against requirements/base.txt (mirrors the "security" CI job)
	$(VENV_BIN)/pip-audit -r requirements/base.txt

docker-build: ## Build the Docker image (mirrors the "docker-build" CI job)
	docker build -t medbot-ci-test .

up: ## Rebuild and (re)start all containers in the background (docker compose up -d --build)
	docker compose up -d --build

down: ## Stop and remove all containers (volumes are kept)
	docker compose down

restart: down up ## Shortcut for `make down` followed by `make up`

logs: ## Tail logs from all containers (Ctrl+C to stop)
	@docker compose logs -f --tail 100; status=$$?; [ $$status -eq 130 ] || exit $$status

ps: ## Show container status (e.g. check medbot_core is healthy)
	docker compose ps

clean: ## Remove local test/coverage/cache artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml
	find . -type d -name __pycache__ -not -path "./.git/*" -exec rm -rf {} +
