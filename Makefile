.DEFAULT_GOAL := help
SHELL := /bin/bash
.PHONY: help install test check
help:
	@echo 'make install  Install the pinned helper toolchain'
	@echo 'make check    Test helper boundaries and validate workflow and JavaScript syntax'
install:
	mise trust .mise.toml
	mise install
test:
	python3 -m unittest discover -s tests -v
	python3 -m unittest discover -s template-tests -v
check: test
	python3 helpers/test-caddy.py
	@for file in helpers/*.mjs helpers/*.cjs; do node --check "$$file"; done
	actionlint
	actionlint templates/full-stack/ci.yml templates/app-only/ci.yml templates/full-stack/bun-updates.yml templates/app-only/bun-updates.yml
