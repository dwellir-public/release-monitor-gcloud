# release-monitor-gcloud charm

Machine charm for `gcs-release-monitor`.

## Build

```bash
charmcraft pack
```

## Deploy

Deploy one unit only:

```bash
juju deploy ./release-monitor-gcloud_ubuntu-22.04-amd64.charm --num-units 1
```

Attach/refresh wheel resource:

```bash
juju attach-resource release-monitor-gcloud release-monitor-wheel=/path/to/gcs_release_monitor-0.1.0-py3-none-any.whl
# Later updates:
juju attach-resource release-monitor-gcloud release-monitor-wheel=/path/to/gcs_release_monitor-0.1.1-py3-none-any.whl
```

## Required secrets

Create and grant secrets, then set secret IDs in config:

- `nextcloud-credentials-secret-id` with keys `username`, `app_password` (optional `share_password`)
- `gcs-service-account-secret-id` with key `service_account_json` (required unless anonymous/gcloud-cli mode)
- `webhook-shared-secret-secret-id` with key `shared_secret` (fallback mode)

## Actions

- `run-once`
- `run-once-dry-run`
- `show-effective-config`
- `service-restart`
