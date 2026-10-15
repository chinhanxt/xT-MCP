# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors

VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)

.PHONY: setup lint test test-integration boundaries reuse check release

setup:
	uv sync

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy

boundaries:
	uv run lint-imports

reuse:
	uv run reuse lint

test:
	uv run pytest

# Keycloak 26.6.3 (cổng 19400) và postgres:17-alpine (cổng 19700, model engine) thật trong docker, tự dọn container sau khi chạy.
test-integration:
	uv run pytest -m integration tests/integration --no-cov

check: lint boundaries reuse test $(if $(wildcard desk/node_modules),desk-test)
	scripts/check_spdx.sh .

# Queue checks can run while model/workflow agents are editing their own files.
WORKER_SRC := src/xt/application/jobs.py src/xt/adapters/queue src/xt/entrypoints/cli.py src/xt/entrypoints/worker.py src/xt/entrypoints/worker_config.py
WORKER_UNIT := tests/unit/test_jobs.py tests/unit/test_queue_adapter.py tests/unit/test_queue_cron.py tests/unit/test_worker_cli.py
WORKER_IT := tests/integration/test_worker_queue.py tests/integration/test_worker_queue_review.py tests/integration/worker_handlers.py

.PHONY: check-worker test-worker-integration
check-worker:
	uv run ruff check $(WORKER_SRC) $(WORKER_UNIT) $(WORKER_IT)
	uv run ruff format --check $(WORKER_SRC) $(WORKER_UNIT) $(WORKER_IT)
	uv run mypy $(WORKER_SRC)
	uv run lint-imports
	uv run pytest $(WORKER_UNIT) tests/unit/test_cli.py --no-cov
	uv run reuse lint-file $(filter %.py,$(WORKER_SRC)) src/xt/adapters/queue/*.py $(WORKER_UNIT) $(WORKER_IT) docs/worker-queue.md

# Local postgres:17-alpine only, :19800; fixture removes its container and volume.
test-worker-integration:
	uv run pytest -m integration tests/integration/test_worker_queue.py tests/integration/test_worker_queue_review.py --no-cov

# xT Desk (desk/): Node qua nvm. `make check` chỉ chạy desk-test khi đã desk-setup (desk/node_modules).
NODE := . $$HOME/.nvm/nvm.sh >/dev/null 2>&1 || true;

desk-setup:
	$(NODE) cd desk && npm ci && npx playwright install chromium

desk-test:
	$(NODE) cd desk && npm test && npm run e2e

# Cấu hình trước khi dịch: đọc desk/.env (bản sao của .env.example do bạn sửa); chưa có thì dùng .env.example.
desk-build:
	$(NODE) cd desk && set -a && . ./$$([ -f .env ] && echo .env || echo .env.example) && set +a && npm run build

# Cần ollama chạy sẵn; đọc DESK_LLM_URL/DESK_LLM_MODEL từ desk/.env.example (ollama mặc định)
desk-e2e-local:
	$(NODE) cd desk && set -a && . ./.env.example && set +a && npm run e2e:local-llm

# dist/: wheel, xt-<version>.tar.gz (git archive, loại đề thi VFOSSA), SBOM CycloneDX (python + desk), SHA256SUMS.
# Cần mạng (uvx cyclonedx-bom, npx @cyclonedx/cyclonedx-npm). Chỉ chứa nội dung đã commit.
release:
	scripts/release.sh HEAD

# Triển khai (deploy/Makefile): docs/deploy.md
.PHONY: up-minimal up-full up-public down provision-realm
up-minimal up-full up-public down provision-realm:
	$(MAKE) -C deploy $@

# PostgREST /data: kiểm tra riêng khi các agent khác đang sửa composition/queue.
DATA_SRC := src/xt/adapters/db/postgrest.py src/xt/adapters/db/data_setup.py src/xt/adapters/db/tenancy.py src/xt/adapters/db/migrator.py
DATA_IT := tests/integration/test_headless_data.py tests/integration/headless_stack.py

.PHONY: check-data test-data-integration
check-data:
	uv run ruff check $(DATA_SRC) $(DATA_IT) tests/unit/test_headless_deploy.py
	uv run ruff format --check $(DATA_SRC) $(DATA_IT) tests/unit/test_headless_deploy.py
	uv run mypy $(DATA_SRC)
	uv run lint-imports
	bash -n docs/headless-demo.sh
	uv run reuse lint-file $(DATA_SRC) $(DATA_IT) src/xt/adapters/db/sql/postgrest.sql docs/headless-data.md docs/headless-demo.sh
	uv run pytest tests/unit/model tests/unit/test_headless_deploy.py --no-cov
	$(MAKE) -C deploy test-offline

# Fixture chỉ dùng 1 Postgres + 1 PostgREST local; tên ngẫu nhiên, tự dọn.
test-data-integration:
	uv run pytest -m integration tests/integration/test_headless_data.py --no-cov
