# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# Dùng từ gốc repo (`make up-minimal`, đã chuyển tiếp) hoặc `make -C deploy <target>`.
DC      := docker compose --env-file .env
# `xt serve` chưa có => dùng container tạm trả 503 (xem compose.yaml, xt-api-placeholder)
HAS_SERVE := $(shell grep -qs '"serve"' ../src/xt/entrypoints/cli.py ../src/xt/adapters/cli/main.py && echo yes)

.PHONY: env render up-minimal up-full up-public down clean provision-realm export-realm pgrst-jwks pgrst-issuer provision-data test-offline

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

pgrst-jwks: env   # tải + kiểm JWKS Keycloak cho PostgREST (fail-closed); xong thì: docker compose kill -s USR2 postgrest
	python3 scripts/fetch_jwks.py

provision-data: env   # cấp scope xt-data (optional) cho XT_DATA_CLIENTS; chạy sau provision-realm
	python3 keycloak/provision_data.py

pgrst-issuer:   # issuer mong đợi, dùng cho install_postgrest_role(issuer=...) và so khớp với fetch_jwks
	@python3 -c "import sys,pathlib,os; sys.path.insert(0,'scripts'); import envfile; e={**envfile.load_env(pathlib.Path('.env')),**os.environ}; print(e.get('XT_PGRST_ISSUER') or 'https://%s/realms/%s' % (e['XT_DOMAIN_AUTH'], e.get('XT_REALM') or 'xt'))"

test-offline:   # provision_data + fetch_jwks với Keycloak giả trên loopback (không Docker, không bí mật thật)
	python3 -I -m unittest discover -s tests/offline -t tests/offline
