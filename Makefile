# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors

VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)

.PHONY: setup lint test boundaries reuse check release

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

check: lint boundaries reuse test
	scripts/check_spdx.sh .

release:
	rm -rf dist
	uv build
	git archive --format=tar.gz --prefix=xt-$(VERSION)/ -o dist/xt-$(VERSION).tar.gz HEAD
	@ls -l dist
