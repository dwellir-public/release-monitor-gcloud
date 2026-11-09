# Polling failures in Juju status

Scope: bounded runtime-health fix, based on revision
`fa84989d72e64b62498c1eb83c3662e4e2a3f577`, inspected 2026-11-09.

Production: `hyperborea:jonathanudd@external/release-filter`, application
`release-monitor-gcloud`. The process is active but polling fails with
`RefreshError` (`Invalid JWT Signature`). Full details remain in the journal.

| Requirement/source | Authority/applicability | Before | Disposition | Target | Actual verification |
| --- | --- | --- | --- | --- | --- |
| User: show failures with short descriptions | task-required/runtime health | GAP: only systemd checked | Implement daemon health report and charm status mapping | Blocked on failed/stale health; automatic recovery | PASS: monitor/charm tests and live failed report |
| AGENTS.md: preserve contracts and test changes | repository-required/shared | Existing config/relation/action contracts | Preserve; add optional daemon CLI argument | Existing delivery and actions unchanged | PASS: existing delivery/relation/action tests |
| Charm skill BUILD-001 | required-baseline/packaging | ops, uv plugin and lock present | Preserve packaging | No packaging migration | PASS: built Ubuntu 22.04 amd64 charm |
| Charm skill DEPLOY-001 | required-baseline/integration | GAP: existing pytest-operator suite, not Jubilant | Defer framework migration outside bugfix | Gap remains; no production integration tests | Not executed |

Other charm baseline capabilities are unassessed. No decisions are needed for
these dispositions. Credential replacement is outside this visibility fix.

## Validation and rollout

- `make test-all`: 28 monitor tests and 31 charm tests pass. Coverage includes
  failed polls, recovery, PID mismatch, missing reports, stale reports and safe
  exception summaries. Existing delivery, relation and action tests still pass.
- Targeted `ruff check` and `git diff --check`: pass.
- `make wheel` and `make charm-pack`: pass. Packaged runtime compared byte for
  byte with tested source. Built/tested deployment base: Ubuntu 22.04 amd64 only;
  arm64 not built or live-tested.
- Disposable local model `local:admin/release-monitor-health-20261109` was created
  for a live smoke check. Its machine remained in provisioning, with snapd seed
  jobs preventing cloud-init completion; no local live-test PASS is claimed.
  Test model and container removed afterward. Existing integration suite not run;
  it only covers missing configuration, and no integration tests ran in production.
- Production refreshed to local charm revision 8 and matching wheel in one
  `juju refresh` operation. Existing config, secrets, state and relations preserved.
- Production daemon emitted a failed report with `RefreshError` and its current
  PID. Invoked the charm's `update-status` dispatch to refresh status immediately.
  Verified Juju now reports `blocked` with `Polling failed: RefreshError`;
  the other applications remain active. Subsequent status updates use the
  normal model cadence.
- Live recovery not induced in production; failure-to-success recovery is covered
  by deterministic daemon and charm tests.

Artifacts (SHA-256):

- `release-monitor-gcloud_amd64.charm`:
  `bb5fdec23df8e997a9c1be399c35c46d6c77a9a330abeec746f8fbe04f285b90`
- `gcs_release_monitor-0.1.0-py3-none-any.whl`:
  `96579f5d748e1ade1a03d0754ebf0220d6fd702b9bbed5fb0a244a7940d9f1a6`

Previous deployed artifacts are retained locally under ignored `dist/` as
`previous-release-monitor-gcloud.charm` and `previous-gcs-release-monitor.whl`.
The charm backup excludes Juju's unit-state database. Rollback both together:

```bash
juju refresh -m hyperborea:jonathanudd@external/release-filter release-monitor-gcloud \
  --path ./dist/previous-release-monitor-gcloud.charm \
  --resource release-monitor-wheel=./dist/previous-gcs-release-monitor.whl
```

Manual verification:

```bash
juju status -m hyperborea:jonathanudd@external/release-filter
```

Fixing the invalid GCS service-account signature requires a separate credential
update. This change exposes that failure and continues retrying.
