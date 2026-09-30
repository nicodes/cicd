.DEFAULT_GOAL := help
SHELL := /bin/bash
.PHONY: help install test check fleet-audit
help:
	@echo 'make install  Install the pinned helper toolchain'
	@echo 'make check    Test helper boundaries and validate workflow and JavaScript syntax'
install:
	mise trust .mise.toml
	mise install
test:
	python3 -m unittest discover -s tests -v
	python3 -m unittest discover -s template-tests -v
# The cross-repository read, run by a person rather than by CI.
#
# NOT part of `make check` and not a scheduled job: it reads nine private
# repositories in three organisations, which this repository's own token
# cannot do. The automated half is helpers/fleet-baseline.mjs, which runs
# inside each product against the baseline in FLEET.json and needs no
# credential at all.
#
# What this adds over that is the question a baseline cannot answer: do the
# image digests agree? Dependabot moves those constantly, so they are not in
# the baseline, and this is how you check them.
fleet-audit:
	python3 helpers/fleet-audit.py

check: test
	python3 helpers/test-caddy.py
	@for file in helpers/*.mjs helpers/*.cjs; do node --check "$$file"; done
	actionlint
	actionlint templates/full-stack/ci.yml templates/app-only/ci.yml templates/full-stack/bun-updates.yml templates/app-only/bun-updates.yml templates/static-web/ci.yml templates/pr-preview.yml
