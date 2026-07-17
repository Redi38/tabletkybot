.PHONY: help ci lint format typecheck test security docker-build clean

# Env vars used by the `test` target — same placeholders as .github/workflows/ci.yml,
# so tests run the same way locally as they do in CI without needing a real .env.
export BOT_TOKEN ?= test_token_placeholder
export NVIDIA_API_KEY ?= test_key_placeholder
export WEBHOOK_HOST ?= https://example.com
export POSTGRES_USER ?= test_user
export POSTGRES_PASSWORD ?= test_password
export POSTGRES_DB ?= test_db
export POSTGRES_HOST ?= localhost
export POSTGRES_PORT ?= 5432
export REDIS_HOST ?= localhost
export REDIS_PORT ?= 6379

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

ci: lint typecheck test security docker-build ## Run every CI job locally, in the same order as GitHub Actions

lint: ## Run ruff lint + format check (mirrors the "lint" CI job)
	ruff check .
	ruff format --check .

format: ## Auto-fix lint issues and reformat code (not run in CI, but fixes what `lint` complains about)
	ruff check --fix .
	ruff format .

typecheck: ## Run mypy (mirrors the "typecheck" CI job)
	mypy . --ignore-missing-imports

test: ## Run pytest with coverage (mirrors the "test" CI job). Needs Postgres+Redis reachable via the vars above.
	pytest -v --tb=short --cov=services --cov=database --cov-report=term-missing --cov-report=xml

security: ## Run pip-audit against requirements.txt (mirrors the "security" CI job)
	pip-audit -r requirements.txt

docker-build: ## Build the Docker image (mirrors the "docker-build" CI job)
	docker build -t medbot-ci-test .

clean: ## Remove local test/coverage/cache artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml
	find . -type d -name __pycache__ -not -path "./.git/*" -exec rm -rf {} +
