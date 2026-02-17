#!/usr/bin/env python3

from __future__ import annotations

import logging
import subprocess as sp
from pathlib import Path
from typing import Any

import ops
from ops.framework import StoredState
from ops.model import BlockedStatus

from constants import APP_DIR, CONFIG_PATH, GCS_CREDENTIALS_PATH
from models import ReconcileError, SecretBundle, WebhookResolution
from release_monitor_gcloud import ReleaseMonitorRuntime
from rendering import build_render_config as _build_render_config
from rendering import parse_json_array_option

logger = logging.getLogger(__name__)


class ReleaseMonitorGcloudCharm(ops.CharmBase):
    _stored = StoredState()

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._stored.set_default(
            config_fingerprint="",
            wheel_digest="",
            wheel_version="",
            webhook_source="",
        )
        self._runtime = ReleaseMonitorRuntime(self)

        framework.observe(self.on.install, self._on_reconcile)
        framework.observe(self.on.start, self._on_reconcile)
        framework.observe(self.on.config_changed, self._on_reconcile)
        framework.observe(self.on.upgrade_charm, self._on_reconcile)
        framework.observe(self.on.update_status, self._on_reconcile)
        framework.observe(self.on.secret_changed, self._on_reconcile)

        relation_events = self.on["release-monitor-webhook"]
        framework.observe(relation_events.relation_created, self._on_reconcile)
        framework.observe(relation_events.relation_changed, self._on_reconcile)
        framework.observe(relation_events.relation_broken, self._on_reconcile)

        framework.observe(self.on.stop, self._on_stop)
        framework.observe(self.on.run_once_action, self._on_run_once_action)
        framework.observe(self.on.run_once_dry_run_action, self._on_run_once_dry_run_action)
        framework.observe(self.on.show_effective_config_action, self._on_show_effective_config_action)
        framework.observe(self.on.service_restart_action, self._on_service_restart_action)

    def _on_reconcile(self, _event: Any) -> None:
        try:
            self._runtime.reconcile()
        except ReconcileError as exc:
            logger.info("reconcile blocked: %s", exc)
            if exc.stop_service:
                self._runtime.stop_service()
            self.unit.status = BlockedStatus(str(exc))
        except Exception:
            logger.exception("reconcile failed")
            self.unit.status = BlockedStatus("reconcile failed; check unit logs")

    def _on_stop(self, _event: Any) -> None:
        self._runtime.stop_service()

    def _on_run_once_action(self, event: ops.ActionEvent) -> None:
        self._runtime.run_once_action(event, dry_run=False)

    def _on_run_once_dry_run_action(self, event: ops.ActionEvent) -> None:
        self._runtime.run_once_action(event, dry_run=True)

    def _on_show_effective_config_action(self, event: ops.ActionEvent) -> None:
        self._runtime.show_effective_config_action(event)

    def _on_service_restart_action(self, event: ops.ActionEvent) -> None:
        self._runtime.service_restart_action(event)

    def _validate_candidate_config(self, candidate_path: Path) -> None:
        self._runtime.validate_candidate_config(candidate_path)

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
        capture_output: bool = False,
    ) -> sp.CompletedProcess[str]:
        return sp.run(args, check=check, capture_output=capture_output, text=True)


if __name__ == "__main__":  # pragma: nocover
    ops.main(ReleaseMonitorGcloudCharm)
