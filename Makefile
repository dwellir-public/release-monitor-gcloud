SHELL := bash

VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
PIP ?= $(VENV)/bin/pip

CONFIG ?= config/local.webhook-only.yaml
WEBHOOK_SECRET ?= gcs-local-integration-secret-20260216
DRY_RUN ?= 0

DIST_DIR ?= dist
CHARM_DIR ?= charm

JUJU ?= juju
JUJU_MODEL ?= local
APP_NAME ?= release-monitor-gcloud
CHARM_FILE ?= $(shell ls -1t $(CHARM_DIR)/release-monitor-gcloud_*.charm 2>/dev/null | head -n 1)
WHEEL_FILE ?= $(shell ls -1t $(DIST_DIR)/gcs_release_monitor-*.whl 2>/dev/null | head -n 1)
RELEASE_MONITOR_WHEEL ?=

.PHONY: \
	help \
	bootstrap bootstrap-charm \
	test test-charm-unit test-charm-integration test-all \
	preflight configure-local-release-filter local-test local-test-dry-run \
	wheel wheel-path \
	charm-pack charm-path \
	charm-deploy charm-deploy-with-wheel charm-refresh charm-attach-wheel \
	charm-status charm-run-once charm-run-once-dry-run

help:
	@echo "Core setup/build/test:"
	@echo "  make bootstrap               Create venv and install monitor dev dependencies."
	@echo "  make bootstrap-charm         Install charm unit/integration dependencies into the same venv."
	@echo "  make test                    Run monitor unit tests (tests/)."
	@echo "  make test-charm-unit         Run charm unit tests (charm/tests/unit)."
	@echo "  make test-charm-integration  Run charm integration tests (set RELEASE_MONITOR_WHEEL=/abs/path.whl)."
	@echo "  make test-all                Run monitor tests + charm unit tests."
	@echo ""
	@echo "Artifacts:"
	@echo "  make wheel                   Build gcs-release-monitor wheel into dist/."
	@echo "  make wheel-path              Print latest wheel path."
	@echo "  make charm-pack              Build charm artifact in charm/ using charmcraft."
	@echo "  make charm-path              Print latest charm path."
	@echo ""
	@echo "Charm deploy/refresh:"
	@echo "  make charm-deploy            Deploy latest charm artifact (without resource)."
	@echo "  make charm-deploy-with-wheel Deploy latest charm artifact and latest wheel resource."
	@echo "  make charm-refresh           Refresh existing app with latest charm artifact."
	@echo "  make charm-attach-wheel      Attach latest wheel resource to existing app."
	@echo "  make charm-status            Show juju status for APP_NAME."
	@echo "  make charm-run-once          Run 'run-once' charm action."
	@echo "  make charm-run-once-dry-run  Run 'run-once-dry-run' charm action."
	@echo ""
	@echo "Local monitor smoke flow:"
	@echo "  make local-test              Configure local release-filter snap + run one monitor cycle (default config is webhook_only)."
	@echo "  make local-test DRY_RUN=1    Same as local-test, monitor runs in dry-run mode."
	@echo "  make local-test-dry-run      Shortcut for DRY_RUN=1."
	@echo ""
	@echo "Variables:"
	@echo "  VENV=$(VENV)"
	@echo "  JUJU_MODEL=$(JUJU_MODEL)"
	@echo "  APP_NAME=$(APP_NAME)"
	@echo "  CONFIG=$(CONFIG)"
	@echo "  WEBHOOK_SECRET=$(WEBHOOK_SECRET)"

bootstrap:
	@test -d "$(VENV)" || python3 -m venv "$(VENV)"
	. "$(VENV)/bin/activate" && pip install --upgrade pip
	. "$(VENV)/bin/activate" && pip install -e .[dev]

bootstrap-charm:
	@test -d "$(VENV)" || python3 -m venv "$(VENV)"
	. "$(VENV)/bin/activate" && pip install "ops[testing]>=3.0" "PyYAML>=6.0" "pytest-operator" "juju"

test:
	. "$(VENV)/bin/activate" && python -m pytest -q tests

test-charm-unit:
	. "$(VENV)/bin/activate" && cd "$(CHARM_DIR)" && PYTHONPATH=src python -m pytest -q tests/unit

test-charm-integration:
	@test -n "$(RELEASE_MONITOR_WHEEL)" || (echo "Set RELEASE_MONITOR_WHEEL=/abs/path/to/gcs_release_monitor-*.whl"; exit 1)
	@test -f "$(RELEASE_MONITOR_WHEEL)" || (echo "Wheel not found: $(RELEASE_MONITOR_WHEEL)"; exit 1)
	. "$(VENV)/bin/activate" && cd "$(CHARM_DIR)" && RELEASE_MONITOR_WHEEL="$(RELEASE_MONITOR_WHEEL)" PYTHONPATH=src python -m pytest -q tests/integration -s

test-all: test test-charm-unit

preflight:
	@test -f "$(CONFIG)" || (echo "Missing config file: $(CONFIG)"; exit 1)
	@test -x "./scripts/configure-local-release-filter.sh" || (echo "Missing executable script: scripts/configure-local-release-filter.sh"; exit 1)
	@test -x "$(VENV)/bin/gcs-release-monitor" || (echo "Missing $(VENV)/bin/gcs-release-monitor. Run: make bootstrap"; exit 1)

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

wheel:
	@mkdir -p "$(DIST_DIR)"
	. "$(VENV)/bin/activate" && pip wheel . --no-deps -w "$(DIST_DIR)"
	@echo "Wheel artifacts are in $(DIST_DIR)/"

wheel-path:
	@test -n "$(WHEEL_FILE)" || (echo "No wheel found in $(DIST_DIR)/. Run: make wheel"; exit 1)
	@echo "$(WHEEL_FILE)"

charm-pack:
	cd "$(CHARM_DIR)" && charmcraft pack

charm-path:
	@test -n "$(CHARM_FILE)" || (echo "No charm artifact found in $(CHARM_DIR)/. Run: make charm-pack"; exit 1)
	@echo "$(CHARM_FILE)"

charm-deploy:
	@test -n "$(CHARM_FILE)" || (echo "No charm artifact found. Run: make charm-pack"; exit 1)
	$(JUJU) deploy --model "$(JUJU_MODEL)" "$(CHARM_FILE)" --application "$(APP_NAME)" --num-units 1

charm-deploy-with-wheel:
	@test -n "$(CHARM_FILE)" || (echo "No charm artifact found. Run: make charm-pack"; exit 1)
	@test -n "$(WHEEL_FILE)" || (echo "No wheel found. Run: make wheel"; exit 1)
	$(JUJU) deploy --model "$(JUJU_MODEL)" "$(CHARM_FILE)" --application "$(APP_NAME)" --num-units 1 --resource release-monitor-wheel="$(WHEEL_FILE)"

charm-refresh:
	@test -n "$(CHARM_FILE)" || (echo "No charm artifact found. Run: make charm-pack"; exit 1)
	$(JUJU) refresh --model "$(JUJU_MODEL)" "$(APP_NAME)" --path "$(CHARM_FILE)"

charm-attach-wheel:
	@test -n "$(WHEEL_FILE)" || (echo "No wheel found in $(DIST_DIR)/. Run: make wheel"; exit 1)
	$(JUJU) attach-resource --model "$(JUJU_MODEL)" "$(APP_NAME)" release-monitor-wheel="$(WHEEL_FILE)"

charm-status:
	$(JUJU) status --model "$(JUJU_MODEL)" "$(APP_NAME)"

charm-run-once:
	$(JUJU) run --model "$(JUJU_MODEL)" "$(APP_NAME)/0" run-once --wait

charm-run-once-dry-run:
	$(JUJU) run --model "$(JUJU_MODEL)" "$(APP_NAME)/0" run-once-dry-run --wait
