# GCS secret rotation

Scope: restart the monitor after a consumed GCS credential change. Evidence:
`ce2c5b89e54efd603a5003e12eda69022cd29e0d` on `origin/main`, inspected
2026-11-09. Production
showed a rewritten credential file with a process still running from before the
secret update. The GCS client loads credentials when it is constructed.

| Requirement/source | Authority/applicability | Before | Disposition | Target | Actual verification |
| --- | --- | --- | --- | --- | --- |
| User: apply GCS secret updates automatically | task-required/runtime | GAP: fingerprint includes credential path only | Include credential content digest in restart fingerprint | Changed credentials restart service | PASS: secret-changed event test |
| AGENTS.md: preserve contracts and test behavior | repository-required/shared | Config, actions and relations already established | Preserve; test unchanged content and retry | No repeated restarts for unchanged credentials; failed restart retried | PASS: unchanged-content and restart-retry tests |
| Charm skill BUILD-001 | required-baseline/packaging | uv plugin, explicit ops dependency and lock present | Preserve | Packaging unchanged | PASS: Ubuntu 22.04 amd64 charm built |
| Charm skill DEPLOY-001 | required-baseline/integration | GAP: existing suite uses pytest-operator rather than Jubilant | Defer unrelated framework migration | Gap remains | Integration not executed |

Other charm capabilities are unassessed. No user decisions are needed for these
dispositions. Only the resulting fingerprint is persisted in charm state;
credential text is not added to logs, status or reports. Configuration keys,
secret fields and the monitor wheel interface remain unchanged.

## Verification

- `make test-all`: 28 monitor tests and 35 charm tests pass.
- Regression confirmed by temporarily ignoring credentials in the fingerprint
  inside the test process: both changed-credential cases fail; both unchanged
  cases pass. The monkeypatch writes no source files.
- Targeted Ruff checks and `git diff --check`: pass.
- No live integration tests or production changes performed for this follow-up.
  Production credential rotation is not used as a test fixture.

## Rollout and manual check

Build and refresh the charm using the repository's normal `make charm-pack` and
`make charm-refresh JUJU_MODEL=<controller:model>` workflow. The existing wheel
already supports this change and does not need replacement. The expanded
fingerprint causes one restart on the first successful reconciliation after
upgrade.

For subsequent GCS secret updates, the secret-changed hook reloads the credential
file and restarts the monitor when its content changes. The first successful poll
and following status update should restore active status. Reapplying identical
content should leave the process running. A failed restart does not commit the new
fingerprint, so later reconciliation retries it.

Built Ubuntu 22.04 amd64 charm SHA-256: `e14728ca84de0dd18cdf34eadfe08ec3168403fab3a4a0e3a38ddefbf3b1cc6e`. Packed runtime matches the tested source. Arm64 was not built or live-tested.
