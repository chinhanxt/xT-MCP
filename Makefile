# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors

VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)

.PHONY: setup lint test test-integration boundaries reuse check release verify-clean

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

# Local postgres:17-alpine only, random loopback port; fixture removes its container and volume.
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
# Như giám khảo: clone HEAD ra /tmp, setup + check + release + kiểm tarball, rồi xóa (scripts/verify_clean_clone.sh).
verify-clean:
	scripts/verify_clean_clone.sh

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

# ---- E2E toàn bộ E0-E11 (docs/e2e.md): stack compose minimal THẬT, volume mới, qua APISIX, không mock ----
# Bằng chứng: e2e/reports/<timestamp>/ (E<n>.json/.md, summary.md). Bí mật user test: ~/.config/xt/e2e/ (0600).
# Heavy-stack: e2e-up chờ RAM available >= 4 GiB và không có Keycloak/Ollama khác. E2E_KEEP=1 giữ stack sau khi chạy.
E2E := cd e2e && PYTHONPATH=. uv run --project ..
.PHONY: e2e-up e2e-down demo-seed e2e e2e-run e2e-llm
e2e-up:
	$(E2E) python -m xte2e.stack up

e2e-down:
	$(E2E) python -m xte2e.stack down

demo-seed:
	$(E2E) python -m xte2e.seed

e2e-run:   # chạy kịch bản trên stack đang có (đã demo-seed)
	$(E2E) pytest -c pytest.ini -m "e2e and not llm" $(E2E_ARGS)

e2e:
	@rc=0; $(MAKE) e2e-up; $(MAKE) demo-seed; $(MAKE) e2e-run || rc=$$?; \
	  [ "$${E2E_KEEP:-0}" = 1 ] || $(MAKE) e2e-down; exit $$rc

e2e-llm:   # E1: Desk + Ollama qwen3.5:4b (slot heavy riêng); cần stack đã up + demo-seed
	$(E2E) python -m xte2e.stack preflight
	$(E2E) pytest -c pytest.ini -m llm $(E2E_ARGS)
