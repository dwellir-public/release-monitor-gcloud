# AGENTS.md

This file describes the repository components and the standard operator/developer workflows.

## Repository components

1. Monitor application (`gcs-release-monitor`)
   - Location: `src/gcs_release_monitor/`
   - Purpose: poll GCS, mirror artifacts to Nextcloud, emit signed release webhooks.
   - Tests: `tests/`

2. Charm (`release-monitor-gcloud`)
   - Location: `charm/`
   - Purpose: run monitor as a single-unit machine charm with `systemd`, config rendering, and secret/relation handling.
   - Main files:
     - `charm/charmcraft.yaml`: charm metadata, resources, config options, actions.
     - `charm/src/charm.py`: event wiring/orchestration.
     - `charm/src/release_monitor_gcloud.py`: runtime domain logic.
     - `charm/src/release_filter_webhook_requirer.py`: relation contract resolver.
     - `charm/src/rendering.py`: config/systemd rendering helpers.
     - `charm/src/constants.py`: constant paths/names.
     - `charm/src/models.py`: dataclasses + reconcile errors.
   - Tests:
     - Unit: `charm/tests/unit/`
     - Integration: `charm/tests/integration/`

3. Utility and docs
   - `scripts/`: local helper scripts (for local release-filter setup and smoke runs).
   - `config/`: local example monitor configs.
   - `docs/`: plans/design docs.
   - Root `Makefile`: canonical entrypoint for setup/build/test/deploy flows.

## Build, test, deploy command map

- Bootstrap monitor env: `make bootstrap`
- Install charm test deps: `make bootstrap-charm`
- Monitor tests: `make test`
- Charm unit tests: `make test-charm-unit`
- Charm integration tests: `RELEASE_MONITOR_WHEEL=/abs/path.whl make test-charm-integration`
- Build monitor wheel: `make wheel`
- Build charm: `make charm-pack`
- Deploy charm: `make charm-deploy` or `make charm-deploy-with-wheel`
- Refresh charm: `make charm-refresh`
- Refresh wheel resource: `make charm-attach-wheel`

## SOP 1: Build and run tests

1. `make bootstrap`
2. `make bootstrap-charm`
3. `make test`
4. `make test-charm-unit`
5. (Optional) `make test-all`

## SOP 2: Run integration tests

1. Build or locate a wheel:
   - `make wheel`
   - `make wheel-path` (copy absolute path)
2. Run integration tests with wheel env var:
   - `RELEASE_MONITOR_WHEEL=/abs/path/to/gcs_release_monitor-<version>.whl make test-charm-integration`
3. Ensure target Juju/controller context is correct before running integration tests.

## SOP 3: Deploy or refresh the charm

1. Build charm:
   - `make charm-pack`
2. Deploy:
   - `make charm-deploy-with-wheel JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud`
   - or deploy charm only: `make charm-deploy JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud`
3. Configure charm with required options and secret IDs via `juju config`.
4. Check status:
   - `make charm-status JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud`
5. Refresh charm code later:
   - `make charm-pack`
   - `make charm-refresh JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud`

## SOP 4: Deploy or refresh the gcloud monitoring wheel resource

1. Build latest wheel:
   - `make wheel`
2. Attach/update resource on deployed app:
   - `make charm-attach-wheel JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud`
3. Verify resource and unit status:
   - `juju resources -m <model> release-monitor-gcloud`
   - `make charm-status JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud`

## Security notes

1. Never commit plaintext credentials, service account JSON, or webhook secrets.
2. Use Juju secrets for:
   - `nextcloud-credentials-secret-id`
   - `gcs-service-account-secret-id`
   - `webhook-shared-secret-secret-id`
3. Grant secret access to the application (`juju grant-secret <secret-id> <app-name>`).
