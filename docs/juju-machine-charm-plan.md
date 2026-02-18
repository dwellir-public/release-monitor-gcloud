# Juju Machine Charm Plan: release-monitor-gcloud

## Summary
Deploy `release-monitor-gcloud` as a Juju machine charm that runs `gcs-release-monitor` under `systemd` on exactly one unit, with config rendered to the app's native schema, secrets sourced from Juju secrets, and webhook endpoint/signing data consumed from relation data or configured fallback.

## Locked Decisions
1. Charm type: Juju machine charm.
2. Runtime: `systemd` long-running process.
3. Scaling model: single unit only; no leader/non-leader logic.
4. Install strategy: pinned wheel resource installed into charm-managed venv.
5. Secrets model: credentials and signing material only via Juju secrets.
6. Webhook integration: requirer relation in this repo; provider implementation tracked in sibling repo file `release-filter/charm/release-monitor-webhook-provider-plan.md`.

## Scope
In scope:
1. New machine charm under this repository (`charm/`).
2. Service lifecycle management (`install`, `start`, `stop`, `restart`) via `systemd`.
3. Render `/etc/release-monitor-gcloud/config.yaml` using the app's current config names and shape.
4. Juju secret ingestion for GCS, Nextcloud, and webhook shared secret.
5. Requirer-side relation handling for `release-monitor-webhook`.
6. Unit and integration tests for charm logic and relation wiring.

Out of scope:
1. Kubernetes charm/Pebble migration.
2. GitHub runner-based production polling.
3. Multi-active pollers, leader election, or distributed locking.
4. Provider-side relation changes in `release-filter` (moved to sibling repo plan).

## Interfaces

### Rendered app config contract
The charm always renders the monitor config in the exact app schema consumed by `gcs_release_monitor.config.load_config`:
1. Top level: `poll_interval_seconds`, `state_dir`, `temp_dir`.
2. `gcs`: `bucket`, `anonymous`, `use_gcloud_cli`, `credentials_file`, `include_prefixes`, `include_suffixes`, `include_content_types`.
3. `nextcloud`: `base_url`, `username`, `app_password`, `remote_dir`, `verify_tls`, `create_public_share`, `share_password`, `share_expire_days`, `share_permissions`.
4. `webhook`: `url`, `shared_secret`, `timeout_seconds`, `verify_tls`.
5. `chain`: `organization`, `repository`, `common_name`, `extra_info`, `client_name`, `chain_ids`, `genesis_hashes`.
6. `release_defaults`: `urgent`, `priority`, `due_date`.
7. `artifact_selection`: `enabled`, `fallback_to_archive`, `default_binary_patterns`, `default_genesis_patterns`, `rules`.

### Charm config option naming
Juju config keys use app names with section prefixes and hyphens replacing underscores. Rendering maps them back to the app names above.
1. Example mapping: `poll-interval-seconds` -> `poll_interval_seconds`.
2. Example mapping: `gcs-bucket` -> `gcs.bucket`.
3. Example mapping: `release-defaults-due-date` -> `release_defaults.due_date`.
4. Example mapping: `artifact-selection-default-binary-patterns` -> `artifact_selection.default_binary_patterns`.

### Charm config keys
Required keys:
1. `gcs-bucket`
2. `nextcloud-base-url`
3. `nextcloud-remote-dir`
4. `chain-organization`
5. `chain-repository`

Optional keys with defaults:
1. `poll-interval-seconds=900`
2. `state-dir=/var/lib/release-monitor-gcloud/state`
3. `temp-dir=/var/lib/release-monitor-gcloud/tmp`
4. `gcs-anonymous=false`
5. `gcs-use-gcloud-cli=false`
6. `gcs-include-prefixes=[]` (JSON array string)
7. `gcs-include-suffixes=[]` (JSON array string; empty means app defaults)
8. `gcs-include-content-types=[]` (JSON array string; empty means app defaults)
9. `nextcloud-verify-tls=true`
10. `nextcloud-create-public-share=true`
11. `nextcloud-share-expire-days=0` (0 means omit in rendered config)
12. `nextcloud-share-permissions=1`
13. `webhook-url=""` (fallback only when no relation)
14. `webhook-timeout-seconds=10`
15. `webhook-verify-tls=true`
16. `chain-common-name=""` (empty maps to `chain-repository`)
17. `chain-extra-info=""`
18. `chain-client-name=""`
19. `chain-ids=[]` (JSON array string)
20. `chain-genesis-hashes=[]` (JSON array string)
21. `release-defaults-urgent=false`
22. `release-defaults-priority=3`
23. `release-defaults-due-date=P2D`
24. `artifact-selection-enabled=true`
25. `artifact-selection-fallback-to-archive=true`
26. `artifact-selection-default-binary-patterns=[]` (JSON array string)
27. `artifact-selection-default-genesis-patterns=[]` (JSON array string)
28. `artifact-selection-rules=[]` (JSON array of rule objects)
29. `log-level=INFO`

### Juju secrets consumed
Secret IDs are configured in charm config and values are read from secret content fields matching app names:
1. `nextcloud-credentials-secret-id` (required): keys `username`, `app-password`, optional `share-password`.
2. `gcs-service-account-secret-id` (optional): key `service-account-json`.
3. `webhook-shared-secret-secret-id` (optional fallback): key `shared-secret`.

Secret handling rules:
1. If `gcs-anonymous=false` and `gcs-use-gcloud-cli=false`, `gcs-service-account-secret-id` is required.
2. Service account JSON is written to `/var/lib/release-monitor-gcloud/secrets/gcs-service-account.json` with `0600` permissions and rendered as `gcs.credentials_file`.
3. Nextcloud username/password are always sourced from `nextcloud-credentials-secret-id`.
4. Webhook shared secret precedence is relation secret first, then relation plaintext fallback, then `webhook-shared-secret-secret-id`.

### Relation contract (`release-monitor-webhook`) requirer side
Requirer (`release-monitor-gcloud`) consumes:
1. `protocol_version` required, value `release-monitor-webhook.v1`.
2. `webhook_url` required for relation-provided endpoint.
3. `secret_id` optional and preferred; points to Juju secret containing `shared-secret`.
4. `webhook_secret` optional plaintext fallback for local/dev use only.

Resolution rules:
1. Webhook URL precedence: relation `webhook_url` then config `webhook-url`.
2. Webhook secret precedence: relation `secret_id` then relation `webhook_secret` then config `webhook-shared-secret-secret-id`.
3. If no URL or secret resolves after precedence, unit goes `BlockedStatus` with explicit missing field message.
4. Relation change/break triggers full reconcile and conditional service restart.

## Service Model
Service name: `release-monitor-gcloud.service`

`ExecStart`:
`/opt/release-monitor-gcloud/venv/bin/gcs-release-monitor --config /etc/release-monitor-gcloud/config.yaml --log-level <value from charm config key log-level>`

Unit directives:
1. `Restart=always`
2. `RestartSec=10`
3. `User=release-monitor`
4. `Group=release-monitor`
5. `WorkingDirectory=/var/lib/release-monitor-gcloud`
6. `StateDirectory=release-monitor-gcloud`
7. `NoNewPrivileges=true`
8. `ProtectSystem=full`
9. `ProtectHome=true`

No `EnvironmentFile` is used.

## Implementation Phases

### Phase 1: Scaffold charm
1. Create `charm/` with `charmcraft.yaml`, `metadata.yaml`, `config.yaml`, `actions.yaml`, `src/charm.py`, templates, and tests.
2. Define charm resource `release-monitor-wheel` and document attach/refresh commands.
3. Add templates for systemd unit and rendered monitor config.
4. Add charm action `run-once`.
5. Add charm action `run-once-dry-run`.
6. Add charm action `show-effective-config`.
7. Add charm action `service-restart`.

### Phase 2: Runtime install and reconcile
1. Create system user/group `release-monitor` if absent.
2. Create venv at `/opt/release-monitor-gcloud/venv`.
3. Fetch `release-monitor-wheel` resource and install with pip into the venv.
4. Verify installed package version from wheel metadata and fail reconcile if missing/unreadable resource.
5. Create runtime directory `/var/lib/release-monitor-gcloud/state`.
6. Create runtime directory `/var/lib/release-monitor-gcloud/tmp`.
7. Create runtime directory `/var/lib/release-monitor-gcloud/secrets`.
8. Create runtime directory `/etc/release-monitor-gcloud`.
9. Render candidate config to `/etc/release-monitor-gcloud/config.yaml.new`.
10. Validate candidate config by importing and running `load_config` from the installed package.
11. Atomically replace `/etc/release-monitor-gcloud/config.yaml` only after validation.
12. Install/refresh systemd unit and run `daemon-reload`.

### Phase 3: Event handling and status
1. Implement a single idempotent `reconcile()` called from `install`, `config-changed`, `start`, `upgrade-charm`, relation events, and secret-changed events.
2. On `reconcile()`, parse charm config and JSON array fields with explicit validation errors.
3. Enforce single-unit deployment: if application unit count is greater than one, set `BlockedStatus("single-unit charm; scale to 1")` and stop local service.
4. Restart service only when effective config, secret material, wheel revision, or relation-resolved webhook values change.
5. Keep service disabled/stopped on missing required inputs.

Status policy:
1. `BlockedStatus` for missing required config, missing required secret fields, invalid JSON option values, invalid relation contract, invalid rendered config, or scale greater than one.
2. `WaitingStatus` while runtime install or resource fetch is in progress.
3. `ActiveStatus` only when unit count is one, config is valid, and service is active.

### Phase 4: Relation integration hardening
1. Add explicit relation data validator for required keys and protocol version.
2. Add relation broken behavior: fall back to config-provided `webhook-url` and `webhook-shared-secret-secret-id` when present.
3. Emit clear status messages indicating whether webhook source is relation or local fallback.

## Failure Modes and Handling
1. Missing secrets/config: `BlockedStatus` with exact key/field name.
2. Invalid JSON in list/rule options: keep last known-good config, block, and do not restart.
3. Invalid rendered app config: keep last known-good config via atomic swap strategy and block.
4. Resource missing or invalid wheel: block and keep previous running service untouched.
5. Relation absent: block unless both fallback webhook URL and fallback webhook secret are present.
6. Nextcloud/GCS/webhook transient failures: service remains running and retries on next poll.
7. Reboot: systemd auto-starts service with last known-good config.

## Tests

### Unit tests
1. Install event creates user, venv, directories, and unit file.
2. Missing `release-monitor-wheel` resource produces `BlockedStatus`.
3. Config rendering maps hyphenated charm options to exact app config keys.
4. Required secret fields are enforced and rendered correctly.
5. Relation precedence over fallback config is enforced for webhook URL/secret.
6. Relation broken fallback behavior is deterministic.
7. Invalid candidate config does not replace last known-good config.
8. Single-unit guard blocks scale greater than one.
9. `run-once` and `run-once-dry-run` actions invoke the CLI with expected flags.

### Integration tests (Juju `local` controller)
1. Deploy `release-filter` and `release-monitor-gcloud` with exactly one `release-monitor-gcloud` unit.
2. Attach `release-monitor-wheel` resource.
3. Add required secrets (`nextcloud-credentials-secret-id`, GCS secret as needed, webhook secret fallback if not related).
4. Relate monitor charm to `release-filter` relation and verify resolved webhook URL/secret source.
5. Run `run-once-dry-run` action and verify action output and no state mutation.
6. Run `run-once` with a single object and verify Nextcloud upload and one webhook event.
7. Verify reboot/restart behavior does not duplicate processed releases.

Acceptance criteria:
1. Rendered config is accepted by `load_config` and uses app-native key names.
2. New bucket artifact yields uploaded files and one webhook release event.
3. Webhook payload contains uploaded artifact links and release-notes metadata when present.
4. Restart/reboot does not duplicate already processed `object_name#generation`.
5. Unit status messages are actionable for operator troubleshooting.
6. Scaling above one unit is blocked with clear operator guidance.

## Local Testing Inputs (provided)
Use these for local-only testing on controller `local`:
1. Juju controller: `local`
2. GCS auth file: `/home/jonathan/Downloads/megaeth-rpc-v2.0.15/artifact-bucket-key.json`
3. Nextcloud test user: `jonathan`

Security notes:
1. Do not commit raw secret values (Nextcloud app password, webhook secret, SA JSON contents) into repository files.
2. Inject credentials via Juju secrets and relation secret IDs during tests.
3. Do not persist secret plaintext in relation data outside local/dev fallback usage.

## Rollout
1. Stage in local model first.
2. Run soak with production-like poll interval.
3. Promote to target model with one unit.
4. Keep deployment at one unit; scaling beyond one is intentionally unsupported.
