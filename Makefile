SHELL := bash

VENV ?= .venv
CONFIG ?= config/local.yaml
WEBHOOK_SECRET ?= gcs-local-integration-secret-20260216
DRY_RUN ?= 0

.PHONY: help preflight configure-local-release-filter local-test local-test-dry-run test

help:
	@echo "Targets:"
	@echo "  make local-test                Configure local release-filter snap and run one monitor cycle."
	@echo "  make local-test DRY_RUN=1      Same as local-test but monitor runs in --dry-run mode."
	@echo "  make local-test-dry-run        Shortcut for DRY_RUN=1."
	@echo "  make test                      Run unit tests."
	@echo ""
	@echo "Variables:"
	@echo "  CONFIG=config/local.yaml"
	@echo "  WEBHOOK_SECRET=$(WEBHOOK_SECRET)"
	@echo "  VENV=.venv"

preflight:
	@test -f "$(CONFIG)" || (echo "Missing config file: $(CONFIG)"; exit 1)
	@test -x "./scripts/configure-local-release-filter.sh" || (echo "Missing executable script: scripts/configure-local-release-filter.sh"; exit 1)
	@test -x "$(VENV)/bin/gcs-release-monitor" || (echo "Missing $(VENV)/bin/gcs-release-monitor. Create venv and run: . $(VENV)/bin/activate && pip install -e .[dev]"; exit 1)

configure-local-release-filter: preflight
	./scripts/configure-local-release-filter.sh "$(WEBHOOK_SECRET)"

local-test: configure-local-release-filter
ifeq ($(DRY_RUN),1)
	. "$(VENV)/bin/activate" && gcs-release-monitor --config "$(CONFIG)" --once --dry-run
else
	. "$(VENV)/bin/activate" && gcs-release-monitor --config "$(CONFIG)" --once
endif

local-test-dry-run:
	$(MAKE) local-test DRY_RUN=1

test:
	. "$(VENV)/bin/activate" && pytest -q
