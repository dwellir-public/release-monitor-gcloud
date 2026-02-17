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

## Current behavior caveat (webhook-only local testing)

Current monitor behavior has no mode that both skips Nextcloud upload and still sends webhook:

1. `run-once` uploads, then webhook.
2. `run-once-dry-run` skips upload and skips webhook.

Any SOP requiring webhook delivery currently also requires a reachable Nextcloud target.

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

Note:
Current integration tests intentionally verify blocked-state behavior and do not exercise Nextcloud upload or webhook delivery.

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

Local test setup with `release-filter`:

1. Deploy/prepare `release-filter` in the same model and enable webhook ingestion.
2. Configure monitor `webhook-url` to the `release-filter` endpoint.
3. Configure matching webhook shared secret between both services.

## SOP 4: Deploy or refresh the gcloud monitoring wheel resource

1. Build latest wheel:
   - `make wheel`
2. Attach/update resource on deployed app:
   - `make charm-attach-wheel JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud`
3. Verify resource and unit status:
   - `juju resources -m <model> release-monitor-gcloud`
   - `make charm-status JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud`

## SOP 5: Required work for true webhook-only local mode

Goal: deploy monitor charm locally, skip upload, still send webhook to `release-filter`.

Required implementation steps:

1. Add monitor config mode (for example `delivery_mode: full|webhook_only`).
2. Update monitor processing logic to bypass Nextcloud upload in `webhook_only`.
3. Define webhook payload behavior without Nextcloud links in `webhook_only`.
4. Add charm config option and render mapping for the mode.
5. Make Nextcloud secret checks conditional in charm reconcile for `webhook_only`.
6. Add unit/integration tests for mode behavior and regressions.

## Security notes

1. Never commit plaintext credentials, service account JSON, or webhook secrets.
2. Use Juju secrets for:
   - `nextcloud-credentials-secret-id`
   - `gcs-service-account-secret-id`
   - `webhook-shared-secret-secret-id`
3. Grant secret access to the application (`juju grant-secret <secret-id> <app-name>`).
