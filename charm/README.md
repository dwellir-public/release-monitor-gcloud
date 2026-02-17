# release-monitor-gcloud charm

Machine charm for `gcs-release-monitor`.

This charm:

1. Deploys `gcs-release-monitor` on a single machine unit.
2. Installs a pinned wheel resource into a charm-managed venv.
3. Renders `/etc/release-monitor-gcloud/config.yaml`.
4. Runs and supervises the monitor via `systemd`.

## Source structure

- `src/charm.py`: charm event wiring/orchestration.
- `src/release_monitor_gcloud.py`: runtime/service lifecycle domain logic.
- `src/release_filter_webhook_requirer.py`: relation contract resolution for `release-monitor-webhook`.
- `src/rendering.py`: config and systemd template rendering helpers.
- `src/constants.py`: shared paths/names/constants.
- `src/models.py`: typed data models and reconcile errors.
- `templates/release-monitor-gcloud.service.tmpl`: `systemd` unit template.

## Build charm artifact

From repo root:

```bash
make charm-pack
```

Directly from `charm/`:

```bash
charmcraft pack
```

## Run tests

From repo root:

```bash
make test-charm-unit
```

Integration tests need a wheel path:

```bash
RELEASE_MONITOR_WHEEL=/abs/path/to/gcs_release_monitor-<version>.whl make test-charm-integration
```

## Deploy charm

Recommended flow from repo root:

```bash
make wheel
make charm-pack
make charm-deploy-with-wheel JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud
```

Then set required config and secret IDs using `juju config`.

## Refresh charm

```bash
make charm-pack
make charm-refresh JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud
```

## Refresh wheel resource

```bash
make wheel
make charm-attach-wheel JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud
```

## Actions

- `run-once`
- `run-once-dry-run`
- `show-effective-config`
- `service-restart`
