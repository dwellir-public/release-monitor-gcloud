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

The target checks that the active Juju controller is `local` and refuses execution otherwise.

## Deploy charm

Recommended flow from repo root:

```bash
make wheel
make charm-pack
make charm-deploy-with-wheel JUJU_MODEL=<model> APP_NAME=release-monitor-gcloud
```

Then set required config and secret IDs using `juju config`.

Local testing default:

```bash
juju config -m <model> release-monitor-gcloud delivery-mode=webhook_only
```

In `webhook_only`, Nextcloud config and `nextcloud-credentials-secret-id` are not required.
Use `delivery-mode=full` only when explicitly testing upload/share behavior.

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

## Delivery modes

The charm supports:

1. `delivery-mode=full`:
   - uploads artifacts to Nextcloud
   - optionally creates public shares
   - sends webhook payloads with Nextcloud links
2. `delivery-mode=webhook_only`:
   - skips Nextcloud upload
   - still sends webhook payloads for detected artifacts
   - uses GCS-based links in payload data

For local tests, use `webhook_only` unless the test explicitly requires Nextcloud upload.

## Polling health

The daemon writes `/var/lib/release-monitor-gcloud/health.json` atomically after
each completed polling cycle. On reconciliation (including Juju's periodic
`update-status` hook), the charm reports failures with a short message such as
`Polling failed: RefreshError`. Full exception details stay in the service journal:

```bash
juju ssh -m <model> release-monitor-gcloud/0 \
  'sudo journalctl -u release-monitor-gcloud.service -n 100 --no-pager'
```

Failures in discovery, downloads, artifact processing, Nextcloud or webhook
delivery block the unit while the daemon continues retrying. A complete successful
cycle, including a poll finding no new releases, restores active status.
Missing, malformed or stale reports also block the unit. Reports older than twice
the polling interval plus five minutes are stale; unusually long downloads may
therefore temporarily report stale health. A fresh report from a starting process
shows waiting until its first result. Reports from an earlier process cannot mark
the current process healthy. Status updates follow the model's update-status
cadence, rather than appearing immediately after each poll.

Refresh the charm **and** attach the wheel built from the same source when
installing this feature; older wheels do not support `--health-file`.
`run-once` and dry-run actions do not overwrite the daemon's health report.

## GCS credential rotation

Updating the configured, granted GCS service-account secret with `juju update-secret`
triggers reconciliation. The credential content contributes to the runtime
fingerprint, so changed credentials restart the monitor to reload its GCS client.
A new revision with identical content does not restart the service. If a restart
fails, the next reconciliation retries it.

After upgrading from an older charm, the first reconciliation restarts the service
once to adopt the expanded fingerprint. This fix requires only a charm refresh;
the monitor wheel does not need rebuilding.
