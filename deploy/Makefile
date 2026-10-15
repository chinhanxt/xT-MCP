# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# Dùng từ gốc repo (`make up-minimal`, đã chuyển tiếp) hoặc `make -C deploy <target>`.
DC      := docker compose --env-file .env
# `xt serve` chưa có => dùng container tạm trả 503 (xem compose.yaml, xt-api-placeholder)
HAS_SERVE := $(shell grep -qs '"serve"' ../src/xt/entrypoints/cli.py ../src/xt/adapters/cli/main.py && echo yes)

.PHONY: env render up-minimal up-full up-public down clean provision-realm export-realm

env:
	python3 -c "import sys,pathlib; sys.path.insert(0,'scripts'); import envfile; envfile.ensure_env(pathlib.Path('.'))"

render: env
	python3 scripts/render_apisix.py

ifeq ($(HAS_SERVE),yes)
up-minimal: render
	$(DC) --profile minimal up -d --build
else
up-minimal: render
	@echo ">> 'xt serve' chưa tồn tại: chạy postgres + keycloak + apisix + xt-api-placeholder (503)"
	$(DC) --profile placeholder up -d --build postgres keycloak apisix xt-api-placeholder
endif

up-full: render
	$(DC) --profile minimal --profile full up -d --build

up-public: render
	$(DC) --profile minimal --profile full --profile public up -d --build

down:
	$(DC) --profile '*' down --remove-orphans

clean:   # xóa cả volume (mất dữ liệu)
	$(DC) --profile '*' down --remove-orphans --volumes

provision-realm: env
	python3 keycloak/provision.py

export-realm: env
	python3 keycloak/provision.py --export
