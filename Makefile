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
check: test
	@for file in helpers/*.mjs helpers/*.cjs; do node --check "$$file"; done
	actionlint
