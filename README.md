# gcs-release-monitor

Lightweight polling service for release artifacts published in a Google Cloud Storage bucket.

## What it does

1. Polls the configured GCS bucket every `poll_interval_seconds` (default 900 = 15 minutes).
2. Stores a local snapshot of object metadata (`snapshot-latest.json`).
3. Diffs current snapshot with the previous snapshot to detect new objects.
4. Filters to release artifact candidates using metadata + suffix/content-type checks.
5. Downloads each new artifact archive from GCS.
6. Optionally extracts target files (e.g. binary + genesis) using chain-specific rules.
7. Uploads selected files to Nextcloud via WebDAV (or falls back to uploading the archive).
8. Optionally creates a public Nextcloud share URL.
9. Sends one signed webhook event per release to release-filter, including links to all uploaded artifacts.

GCS access modes:
- `use_gcloud_cli: true`: uses local `gcloud` auth/session (`gcloud storage ls/cp`).
- `anonymous: true`: uses unauthenticated public bucket APIs.
- `credentials_file`: uses a service account JSON key.

The service is idempotent across restarts via `state/state.json` keyed by `object_name#generation`.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

## Configure

```bash
cp config/example.yaml config/local.yaml
# edit config/local.yaml
```

## Run

Single cycle:

```bash
gcs-release-monitor --config config/local.yaml --once
```

Dry run (no upload/webhook/state writes):

```bash
gcs-release-monitor --config config/local.yaml --once --dry-run
```

Continuous:

```bash
gcs-release-monitor --config config/local.yaml
```

Local integration test (configures local `release-filter` snap + runs one cycle):

```bash
make local-test
```

Dry-run variant:

```bash
make local-test DRY_RUN=1
# or
make local-test-dry-run
```

## Webhook payload contract

The monitor posts JSON with these keys:

- `event_type`, `event_version`, `source`
- `chain`
- `release_meta` (`html_url`, `tag_name`)
- `release` (GCS + Nextcloud metadata, including `download_url` and per-upload `uploads[*].download_url` when public shares are enabled)
- `result` (summary/priority fields expected by release-filter consumers)

Signature headers:

- `X-Release-Timestamp`: Unix seconds
- `X-Release-Signature`: `sha256=<hmac>` over `<timestamp>.<json_body>`

## State files

- `state/state.json`: processed object IDs and delivery metadata.
- `state/snapshot-latest.json`: latest object snapshot.
- `state/snapshot-previous.json`: previous object snapshot.

## Nextcloud path layout

Uploads are written as:

- `<remote_dir>/<organization>/<filename>-g<generation>`

## Artifact extraction and fallback

You can define `artifact_selection.rules` per chain to extract specific files from tar archives.
If extraction fails (missing members, parse error, unsupported archive), the monitor falls back to uploading the original archive when `fallback_to_archive: true`.

The example config includes a MegaETH rule:

- binary pattern: `rpc-node-*`
- genesis pattern preference: `mainnet/genesis.json` then `testnet/genesis.json`
