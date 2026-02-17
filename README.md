# release-monitor-gcloud

`release-monitor-gcloud` contains:

1. The Python monitor service (`gcs-release-monitor`) that polls GCS and emits signed release webhooks.
2. A Juju machine charm (`charm/`) that runs the monitor as a `systemd` service and manages config/secrets/resources.

## Repository layout

- `src/gcs_release_monitor/`: monitor application code.
- `tests/`: monitor unit tests.
- `config/`: local monitor config examples.
- `scripts/`: local helper scripts.
- `charm/`: charm source, packaging config, charm tests.
- `docs/`: implementation plans and related docs.

## Prerequisites

- Python 3.10+
- `make`
- `charmcraft` (for charm packaging)
- Juju 3.x + a target model/controller (for deploy/integration)

## Quick commands

Run `make help` for the full list.

- Setup monitor dev env: `make bootstrap`
- Setup charm deps in same env: `make bootstrap-charm`
- Monitor tests: `make test`
- Charm unit tests: `make test-charm-unit`
- Build monitor wheel: `make wheel`
- Build charm artifact: `make charm-pack`

## Step-by-step: checkout to deployed charm (with wheel)

### 1. Checkout and bootstrap

```bash
git clone <repo-url>
cd release-monitor-gcloud
make bootstrap
make bootstrap-charm
```

### 2. Run tests

```bash
make test
make test-charm-unit
```

### 3. Build artifacts

```bash
make wheel
make charm-pack
make wheel-path
make charm-path
```

### 4. Prepare Juju secrets

Create required secrets (example commands):

```bash
# Nextcloud credentials (required only for delivery-mode=full)
juju add-secret nextcloud-creds username='<nextcloud-user>' app_password='<nextcloud-app-password>' share_password='<optional-share-password>'

# GCS service account (required unless gcs-anonymous=true or gcs-use-gcloud-cli=true)
juju add-secret gcs-service-account service_account_json='{"type":"service_account", ...}'

# Fallback webhook secret (required when relation does not provide secret)
juju add-secret webhook-shared shared-secret='<webhook-shared-secret>'
```

Capture secret IDs from `juju list-secrets` and grant them to the app after deploy:

```bash
juju grant-secret <gcs-secret-id> release-monitor-gcloud
juju grant-secret <webhook-secret-id> release-monitor-gcloud
```

Only for `delivery-mode=full`:

```bash
juju grant-secret <nextcloud-secret-id> release-monitor-gcloud
```

### 5. Deploy charm with wheel resource

```bash
make charm-deploy-with-wheel JUJU_MODEL=<model-name> APP_NAME=release-monitor-gcloud
```

### 6. Configure charm

```bash
juju config -m <model-name> release-monitor-gcloud \
  gcs-bucket='<bucket>' \
  chain-organization='<org>' \
  chain-repository='<repo>' \
  gcs-service-account-secret-id='<gcs-secret-id>' \
  webhook-url='https://<release-filter-host>/v1/releases' \
  webhook-shared-secret-secret-id='<webhook-secret-id>'
```

For local testing, use `webhook_only` unless explicitly testing full uploads:

```bash
juju config -m <model-name> release-monitor-gcloud delivery-mode='webhook_only'
```

For `delivery-mode=full`, also set:

```bash
juju config -m <model-name> release-monitor-gcloud \
  nextcloud-base-url='https://<nextcloud-host>' \
  nextcloud-remote-dir='release-mirror' \
  nextcloud-credentials-secret-id='<nextcloud-secret-id>'
```

### 7. Verify deployment

```bash
make charm-status JUJU_MODEL=<model-name> APP_NAME=release-monitor-gcloud
make charm-run-once-dry-run JUJU_MODEL=<model-name> APP_NAME=release-monitor-gcloud
make charm-run-once JUJU_MODEL=<model-name> APP_NAME=release-monitor-gcloud
```

## Refresh procedures

### Refresh charm code

```bash
make charm-pack
make charm-refresh JUJU_MODEL=<model-name> APP_NAME=release-monitor-gcloud
```

### Refresh wheel resource

```bash
make wheel
make charm-attach-wheel JUJU_MODEL=<model-name> APP_NAME=release-monitor-gcloud
```

## Charm integration test

`charm/tests/integration/test_charm.py` expects `RELEASE_MONITOR_WHEEL`:

```bash
RELEASE_MONITOR_WHEEL=/abs/path/to/gcs_release_monitor-<version>.whl make test-charm-integration
```

Guardrails:

1. The target refuses to run unless the current Juju controller is `local`.
2. The wheel path is normalized to an absolute path before running tests.

## Local deploy flow (webhook-only default)

Use this mode for local testing unless explicitly testing Nextcloud upload behavior.

1. Build artifacts:
   - `make wheel`
   - `make charm-pack`
2. Deploy `release-filter` in the same model and enable webhook ingestion (`ingest-webhook-*` settings).
3. Create and grant required monitor secrets:
   - `gcs-service-account-secret-id` (unless `gcs-anonymous=true` or `gcs-use-gcloud-cli=true`)
   - `webhook-shared-secret-secret-id` (if not using relation secret)
4. Deploy monitor with wheel:
   - `make charm-deploy-with-wheel JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud`
5. Configure webhook-only delivery:
   - `juju config -m <model> release-monitor-gcloud delivery-mode=webhook_only`
6. Configure core settings:
   - `juju config -m <model> release-monitor-gcloud gcs-bucket='<bucket>' chain-organization='<org>' chain-repository='<repo>' webhook-url='https://<release-filter-host>/v1/releases' webhook-shared-secret-secret-id='<secret-id>'`
7. Validate:
   - `make charm-status JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud`
   - `make charm-run-once JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud`

## Local monitor-only smoke flow

These targets configure local `release-filter` snap ingestion and run one monitor cycle:

```bash
make local-test
make local-test-dry-run
```

Note:
`make local-test` defaults to `config/local.webhook-only.yaml` (`delivery_mode: webhook_only`). Keep this default for local tests unless you are explicitly testing Nextcloud uploads.

## Related repositories

- `release-monitor-gcloud`: https://github.com/dwellir-public/release-monitor-gcloud
- `release-filter`: https://github.com/dwellir-public/release-filter
