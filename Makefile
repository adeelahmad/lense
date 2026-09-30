# Makefile

BACKEND_DIR=fastapi_backend
FRONTEND_DIR=nextjs-frontend
DOCKER_COMPOSE=docker compose

.PHONY: help
help: ## List the commands
	@awk '/^[a-zA-Z_-]+:.*##/{split($$1, target, ":"); print "  " target[1] "\t" substr($$0, index($$0,"##")+3)}' $(MAKEFILE_LIST)

# Local development
.PHONY: start-backend test-backend lint-backend start-frontend test-frontend lint-frontend openapi worker docs

start-backend: ## Start the API with hot reload (and the OpenAPI watcher)
	cd $(BACKEND_DIR) && ./start.sh

worker: ## Run a job worker against the configured database
	cd $(BACKEND_DIR) && uv run lens worker

test-backend: ## Run the backend tests
	cd $(BACKEND_DIR) && uv run pytest -n auto

lint-backend: ## Lint, format check and type check the backend
	cd $(BACKEND_DIR) && uv run ruff check . && uv run ruff format --check . && uv run mypy

start-frontend: ## Start the web app with hot reload
	cd $(FRONTEND_DIR) && ./start.sh

test-frontend: ## Run the frontend tests
	cd $(FRONTEND_DIR) && pnpm run test

lint-frontend: ## Lint and type check the frontend
	cd $(FRONTEND_DIR) && pnpm run lint && pnpm run tsc

openapi: ## Regenerate the OpenAPI schema and the frontend client
	cd $(BACKEND_DIR) && uv run python -m commands.generate_openapi_schema
	cd $(FRONTEND_DIR) && pnpm run generate-client

docs: ## Serve the documentation
	cd $(BACKEND_DIR) && uv run mkdocs serve -f ../mkdocs.yml

# Docker
.PHONY: docker-up docker-build docker-backend-shell docker-frontend-shell docker-test-backend docker-test-frontend docker-logs-setup

docker-up: ## Start the whole stack
	$(DOCKER_COMPOSE) up

docker-build: ## Build all the images
	$(DOCKER_COMPOSE) build

docker-backend-shell: ## A shell in the backend container
	$(DOCKER_COMPOSE) run --rm backend sh

docker-frontend-shell: ## A shell in the frontend container
	$(DOCKER_COMPOSE) run --rm frontend sh

docker-test-backend: ## Run the backend tests in Docker
	$(DOCKER_COMPOSE) run --rm backend pytest

docker-test-frontend: ## Run the frontend tests in Docker
	$(DOCKER_COMPOSE) run --rm frontend pnpm run test

docker-logs-setup: ## Show the first-admin setup code
	$(DOCKER_COMPOSE) logs backend | grep -i "setup code"
