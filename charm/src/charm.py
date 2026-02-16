#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess as sp
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

import yaml
from ops.charm import ActionEvent, CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, ModelError, SecretNotFoundError

logger = logging.getLogger(__name__)

SERVICE_NAME = "release-monitor-gcloud.service"
RESOURCE_NAME = "release-monitor-wheel"
RELATION_NAME = "release-monitor-webhook"
PROTOCOL_VERSION = "release-monitor-webhook.v1"

APP_USER = "release-monitor"
APP_GROUP = "release-monitor"
APP_DIR = Path("/var/lib/release-monitor-gcloud")
STATE_DIR = APP_DIR / "state"
TEMP_DIR = APP_DIR / "tmp"
SECRETS_DIR = APP_DIR / "secrets"
ETC_DIR = Path("/etc/release-monitor-gcloud")
CONFIG_PATH = ETC_DIR / "config.yaml"
SERVICE_PATH = Path("/etc/systemd/system") / SERVICE_NAME
VENV_DIR = Path("/opt/release-monitor-gcloud/venv")
GCS_CREDENTIALS_PATH = SECRETS_DIR / "gcs-service-account.json"


class ReconcileError(ValueError):
    def __init__(self, message: str, *, stop_service: bool = True):
        super().__init__(message)
        self.stop_service = stop_service


@dataclass(frozen=True)
class SecretBundle:
    nextcloud_username: str
    nextcloud_app_password: str
    nextcloud_share_password: str | None
    gcs_service_account_json: str | None


@dataclass(frozen=True)
class WebhookResolution:
    url: str
    shared_secret: str
    source: str


@dataclass(frozen=True)
class WheelInstall:
    digest: str
    version: str


def parse_json_array_option(raw: str, option_name: str) -> list[Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReconcileError(f"invalid JSON for {option_name}: {exc.msg}", stop_service=False) from exc
    if not isinstance(parsed, list):
        raise ReconcileError(f"{option_name} must be a JSON array", stop_service=False)
    return parsed


def _non_empty(config: dict[str, Any], key: str) -> str:
    value = str(config.get(key, "")).strip()
    if not value:
        raise ReconcileError(f"missing required config: {key}")
    return value


def _parse_chain_ids(raw: list[Any]) -> list[int]:
    chain_ids: list[int] = []
    for item in raw:
        if isinstance(item, bool):
            raise ReconcileError("invalid JSON for chain-ids: booleans are not allowed", stop_service=False)
        try:
            chain_ids.append(int(item))
        except (TypeError, ValueError) as exc:
            raise ReconcileError(
                f"invalid JSON for chain-ids: {item!r} is not an integer", stop_service=False
            ) from exc
    return chain_ids


def _build_render_config(
    config: dict[str, Any],
    secrets: SecretBundle,
    webhook: WebhookResolution,
    *,
    gcs_credentials_file: str | None,
) -> dict[str, Any]:
    chain_repository = _non_empty(config, "chain-repository")
    rules = parse_json_array_option(
        str(config.get("artifact-selection-rules", "[]")), "artifact-selection-rules"
    )
    if any(not isinstance(rule, dict) for rule in rules):
        raise ReconcileError(
            "artifact-selection-rules must be a JSON array of objects", stop_service=False
        )

    nextcloud: dict[str, Any] = {
        "base_url": _non_empty(config, "nextcloud-base-url"),
        "username": secrets.nextcloud_username,
        "app_password": secrets.nextcloud_app_password,
        "remote_dir": _non_empty(config, "nextcloud-remote-dir"),
        "verify_tls": bool(config.get("nextcloud-verify-tls", True)),
        "create_public_share": bool(config.get("nextcloud-create-public-share", True)),
        "share_permissions": int(config.get("nextcloud-share-permissions", 1)),
    }
    if secrets.nextcloud_share_password:
        nextcloud["share_password"] = secrets.nextcloud_share_password

    expire_days = int(config.get("nextcloud-share-expire-days", 0))
    if expire_days > 0:
        nextcloud["share_expire_days"] = expire_days

    chain_ids = _parse_chain_ids(
        parse_json_array_option(str(config.get("chain-ids", "[]")), "chain-ids")
    )

    rendered: dict[str, Any] = {
        "poll_interval_seconds": int(config.get("poll-interval-seconds", 900)),
        "state_dir": str(config.get("state-dir", str(STATE_DIR))),
        "temp_dir": str(config.get("temp-dir", str(TEMP_DIR))),
        "gcs": {
            "bucket": _non_empty(config, "gcs-bucket"),
            "anonymous": bool(config.get("gcs-anonymous", False)),
            "use_gcloud_cli": bool(config.get("gcs-use-gcloud-cli", False)),
            "credentials_file": gcs_credentials_file,
            "include_prefixes": parse_json_array_option(
                str(config.get("gcs-include-prefixes", "[]")), "gcs-include-prefixes"
            ),
            "include_suffixes": parse_json_array_option(
                str(config.get("gcs-include-suffixes", "[]")), "gcs-include-suffixes"
            ),
            "include_content_types": parse_json_array_option(
                str(config.get("gcs-include-content-types", "[]")), "gcs-include-content-types"
            ),
        },
        "nextcloud": nextcloud,
        "webhook": {
            "url": webhook.url,
            "shared_secret": webhook.shared_secret,
            "timeout_seconds": int(config.get("webhook-timeout-seconds", 10)),
            "verify_tls": bool(config.get("webhook-verify-tls", True)),
        },
        "chain": {
            "organization": _non_empty(config, "chain-organization"),
            "repository": chain_repository,
            "common_name": str(config.get("chain-common-name", "")).strip() or chain_repository,
            "extra_info": str(config.get("chain-extra-info", "")),
            "client_name": str(config.get("chain-client-name", "")),
            "chain_ids": chain_ids,
            "genesis_hashes": parse_json_array_option(
                str(config.get("chain-genesis-hashes", "[]")), "chain-genesis-hashes"
            ),
        },
        "release_defaults": {
            "urgent": bool(config.get("release-defaults-urgent", False)),
            "priority": int(config.get("release-defaults-priority", 3)),
            "due_date": str(config.get("release-defaults-due-date", "P2D")),
        },
        "artifact_selection": {
            "enabled": bool(config.get("artifact-selection-enabled", True)),
            "fallback_to_archive": bool(config.get("artifact-selection-fallback-to-archive", True)),
            "default_binary_patterns": parse_json_array_option(
                str(config.get("artifact-selection-default-binary-patterns", "[]")),
                "artifact-selection-default-binary-patterns",
            ),
            "default_genesis_patterns": parse_json_array_option(
                str(config.get("artifact-selection-default-genesis-patterns", "[]")),
                "artifact-selection-default-genesis-patterns",
            ),
            "rules": rules,
        },
    }
    return rendered


def render_service_unit(*, log_level: str) -> str:
    template = Template(
        (Path(__file__).resolve().parent.parent / "templates" / "release-monitor-gcloud.service.tmpl").read_text(
            encoding="utf-8"
        )
    )
    exec_start = (
        f"{VENV_DIR}/bin/gcs-release-monitor --config {CONFIG_PATH} --log-level {log_level}"
    )
    return template.substitute(exec_start=exec_start)


def dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False)


class ReleaseMonitorGcloudCharm(CharmBase):
    _stored = StoredState()

    def __init__(self, *args: Any):
        super().__init__(*args)
        self._stored.set_default(
            config_fingerprint="",
            wheel_digest="",
            wheel_version="",
            webhook_source="",
        )
        self.framework.observe(self.on.install, self._on_reconcile)
        self.framework.observe(self.on.start, self._on_reconcile)
        self.framework.observe(self.on.config_changed, self._on_reconcile)
        self.framework.observe(self.on.upgrade_charm, self._on_reconcile)
        self.framework.observe(self.on.update_status, self._on_reconcile)
        self.framework.observe(self.on.secret_changed, self._on_reconcile)

        relation_events = self.on[RELATION_NAME]
        self.framework.observe(relation_events.relation_created, self._on_reconcile)
        self.framework.observe(relation_events.relation_changed, self._on_reconcile)
        self.framework.observe(relation_events.relation_broken, self._on_reconcile)

        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self.on.run_once_action, self._on_run_once_action)
        self.framework.observe(self.on.run_once_dry_run_action, self._on_run_once_dry_run_action)
        self.framework.observe(self.on.show_effective_config_action, self._on_show_effective_config_action)
        self.framework.observe(self.on.service_restart_action, self._on_service_restart_action)

    def _on_reconcile(self, _event: Any) -> None:
        try:
            self.reconcile()
        except ReconcileError as exc:
            logger.info("reconcile blocked: %s", exc)
            if exc.stop_service:
                self._stop_service()
            self.unit.status = BlockedStatus(str(exc))
        except Exception:
            logger.exception("reconcile failed")
            self.unit.status = BlockedStatus("reconcile failed; check unit logs")

    def _on_stop(self, _event: Any) -> None:
        self._stop_service()

    def reconcile(self) -> None:
        if int(self.app.planned_units()) > 1:
            raise ReconcileError("single-unit charm; scale to 1")

        secrets = self._resolve_secret_bundle()
        webhook = self._resolve_webhook()

        self._ensure_service_user_group()
        self._ensure_runtime_dirs()
        wheel = self._ensure_venv_and_wheel()

        gcs_credentials_file = self._write_gcs_credentials_if_needed(secrets)
        config_map = _build_render_config(
            dict(self.config),
            secrets,
            webhook,
            gcs_credentials_file=gcs_credentials_file,
        )

        candidate_path = Path(f"{CONFIG_PATH}.new")
        config_yaml = dump_yaml(config_map)
        candidate_path.write_text(config_yaml, encoding="utf-8")
        os.chmod(candidate_path, 0o640)

        self._validate_candidate_config(candidate_path)
        os.replace(candidate_path, CONFIG_PATH)

        unit_text = self._install_systemd_unit(str(self.config.get("log-level", "INFO")))

        fingerprint = self._compute_fingerprint(config_yaml, unit_text, wheel.digest)
        restart_required = fingerprint != str(self._stored.config_fingerprint)

        self._ensure_service_running(restart=restart_required)
        if not self._is_service_active():
            raise ReconcileError("service failed to start", stop_service=False)

        self._stored.config_fingerprint = fingerprint
        self._stored.wheel_digest = wheel.digest
        self._stored.wheel_version = wheel.version
        self._stored.webhook_source = webhook.source
        self.unit.status = ActiveStatus(f"service active (webhook: {webhook.source})")

    def _resolve_secret_bundle(self) -> SecretBundle:
        config = dict(self.config)
        nextcloud_secret_id = str(config.get("nextcloud-credentials-secret-id", "")).strip()
        if not nextcloud_secret_id:
            raise ReconcileError("missing required config: nextcloud-credentials-secret-id")
        nextcloud_content = self._read_secret_content(nextcloud_secret_id)

        username = str(nextcloud_content.get("username", "")).strip()
        app_password = str(nextcloud_content.get("app_password", "")).strip()
        share_password = str(nextcloud_content.get("share_password", "")).strip() or None
        if not username:
            raise ReconcileError("missing required secret field username in nextcloud-credentials")
        if not app_password:
            raise ReconcileError("missing required secret field app_password in nextcloud-credentials")

        gcs_service_account_json: str | None = None
        anonymous = bool(config.get("gcs-anonymous", False))
        use_gcloud_cli = bool(config.get("gcs-use-gcloud-cli", False))
        gcs_secret_id = str(config.get("gcs-service-account-secret-id", "")).strip()

        if not anonymous and not use_gcloud_cli:
            if not gcs_secret_id:
                raise ReconcileError("missing required config: gcs-service-account-secret-id")
            gcs_content = self._read_secret_content(gcs_secret_id)
            gcs_service_account_json = str(gcs_content.get("service_account_json", "")).strip()
            if not gcs_service_account_json:
                raise ReconcileError(
                    "missing required secret field service_account_json in gcs-service-account"
                )

        return SecretBundle(
            nextcloud_username=username,
            nextcloud_app_password=app_password,
            nextcloud_share_password=share_password,
            gcs_service_account_json=gcs_service_account_json,
        )

    def _resolve_webhook(self) -> WebhookResolution:
        config = dict(self.config)
        relations = sorted(self.model.relations.get(RELATION_NAME, []), key=lambda rel: rel.id)

        if relations:
            relation = relations[0]
            if relation.app is None:
                raise ReconcileError("invalid relation contract: missing remote application")
            remote_data = relation.data[relation.app]
            protocol_version = str(remote_data.get("protocol_version", "")).strip()
            if protocol_version != PROTOCOL_VERSION:
                raise ReconcileError(
                    f"invalid relation contract: protocol_version must be {PROTOCOL_VERSION}"
                )
            webhook_url = str(remote_data.get("webhook_url", "")).strip()
            if not webhook_url:
                raise ReconcileError("invalid relation contract: missing webhook_url")

            relation_secret_id = str(remote_data.get("secret_id", "")).strip()
            relation_plaintext_secret = str(remote_data.get("webhook_secret", "")).strip()

            if relation_secret_id:
                relation_secret = self._read_secret_content(relation_secret_id)
                shared_secret = str(relation_secret.get("shared_secret", "")).strip()
                if not shared_secret:
                    raise ReconcileError(
                        "missing required secret field shared_secret in relation secret_id"
                    )
                return WebhookResolution(
                    url=webhook_url,
                    shared_secret=shared_secret,
                    source="relation-secret-id",
                )

            if relation_plaintext_secret:
                return WebhookResolution(
                    url=webhook_url,
                    shared_secret=relation_plaintext_secret,
                    source="relation-plaintext",
                )

            fallback_secret_id = str(config.get("webhook-shared-secret-secret-id", "")).strip()
            if fallback_secret_id:
                fallback_content = self._read_secret_content(fallback_secret_id)
                shared_secret = str(fallback_content.get("shared_secret", "")).strip()
                if not shared_secret:
                    raise ReconcileError(
                        "missing required secret field shared_secret in webhook-shared-secret"
                    )
                return WebhookResolution(
                    url=webhook_url,
                    shared_secret=shared_secret,
                    source="relation-url+config-secret",
                )

            raise ReconcileError(
                "missing webhook secret: relation secret_id, relation webhook_secret, or webhook-shared-secret-secret-id"
            )

        fallback_url = str(config.get("webhook-url", "")).strip()
        if not fallback_url:
            raise ReconcileError("missing webhook URL: relation webhook_url or config webhook-url")

        fallback_secret_id = str(config.get("webhook-shared-secret-secret-id", "")).strip()
        if not fallback_secret_id:
            raise ReconcileError("missing required config: webhook-shared-secret-secret-id")

        fallback_content = self._read_secret_content(fallback_secret_id)
        shared_secret = str(fallback_content.get("shared_secret", "")).strip()
        if not shared_secret:
            raise ReconcileError("missing required secret field shared_secret in webhook-shared-secret")

        return WebhookResolution(url=fallback_url, shared_secret=shared_secret, source="config-fallback")

    def _read_secret_content(self, secret_id: str) -> dict[str, str]:
        try:
            secret = self.model.get_secret(id=secret_id)
        except (SecretNotFoundError, ModelError) as exc:
            raise ReconcileError(f"secret not found: {secret_id}") from exc

        try:
            content = secret.peek_content()
        except ModelError:
            content = secret.get_content(refresh=True)

        if not isinstance(content, dict):
            raise ReconcileError(f"invalid secret content for {secret_id}")
        return {str(key): str(value) for key, value in content.items()}

    def _ensure_service_user_group(self) -> None:
        if self._run(["getent", "group", APP_GROUP], check=False).returncode != 0:
            self._run(["groupadd", "--system", APP_GROUP])

        if self._run(["id", "-u", APP_USER], check=False).returncode != 0:
            self._run(
                [
                    "useradd",
                    "--system",
                    "--gid",
                    APP_GROUP,
                    "--home-dir",
                    str(APP_DIR),
                    "--no-create-home",
                    "--shell",
                    "/usr/sbin/nologin",
                    APP_USER,
                ]
            )

    def _ensure_runtime_dirs(self) -> None:
        for path in (APP_DIR, STATE_DIR, TEMP_DIR, SECRETS_DIR, ETC_DIR):
            path.mkdir(parents=True, exist_ok=True)

        for path in (APP_DIR, STATE_DIR, TEMP_DIR, SECRETS_DIR):
            shutil.chown(path, user=APP_USER, group=APP_GROUP)
            os.chmod(path, 0o750)

    def _ensure_venv_and_wheel(self) -> WheelInstall:
        try:
            wheel_path = Path(self.model.resources.fetch(RESOURCE_NAME))
        except (ModelError, RuntimeError) as exc:
            raise ReconcileError(f"missing required resource: {RESOURCE_NAME}", stop_service=False) from exc

        if not wheel_path.exists() or not wheel_path.is_file():
            raise ReconcileError(f"unreadable wheel resource: {wheel_path}", stop_service=False)

        digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
        python_bin = VENV_DIR / "bin" / "python"
        pip_bin = VENV_DIR / "bin" / "pip"
        cli_bin = VENV_DIR / "bin" / "gcs-release-monitor"

        if not python_bin.exists():
            self._run(["python3", "-m", "venv", str(VENV_DIR)])

        install_required = digest != str(self._stored.wheel_digest) or not cli_bin.exists()
        if install_required:
            self._run([str(pip_bin), "install", "--upgrade", "--force-reinstall", str(wheel_path)])

        version = self._installed_package_version(python_bin)
        if not version:
            raise ReconcileError("failed to determine installed gcs-release-monitor version", stop_service=False)

        return WheelInstall(digest=digest, version=version)

    def _installed_package_version(self, python_bin: Path) -> str:
        command = (
            "import importlib.metadata as m; "
            "print(m.version('gcs-release-monitor'))"
        )
        result = self._run([str(python_bin), "-c", command], capture_output=True, check=False)
        if result.returncode != 0:
            raise ReconcileError("failed to read installed wheel metadata", stop_service=False)
        return str(result.stdout).strip()

    def _write_gcs_credentials_if_needed(self, secrets: SecretBundle) -> str | None:
        if secrets.gcs_service_account_json is None:
            if GCS_CREDENTIALS_PATH.exists():
                GCS_CREDENTIALS_PATH.unlink()
            return None

        GCS_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        GCS_CREDENTIALS_PATH.write_text(secrets.gcs_service_account_json, encoding="utf-8")
        os.chmod(GCS_CREDENTIALS_PATH, 0o600)
        shutil.chown(GCS_CREDENTIALS_PATH, user=APP_USER, group=APP_GROUP)
        return str(GCS_CREDENTIALS_PATH)

    def _validate_candidate_config(self, candidate_path: Path) -> None:
        python_bin = VENV_DIR / "bin" / "python"
        command = (
            "from gcs_release_monitor.config import load_config; "
            f"load_config(r'{candidate_path}')"
        )
        result = self._run([str(python_bin), "-c", command], capture_output=True, check=False)
        if result.returncode != 0:
            message = str(result.stderr).strip() or str(result.stdout).strip() or "validation failed"
            raise ReconcileError(f"invalid rendered config: {message}", stop_service=False)

    def _install_systemd_unit(self, log_level: str) -> str:
        unit_text = render_service_unit(log_level=log_level)
        existing = SERVICE_PATH.read_text(encoding="utf-8") if SERVICE_PATH.exists() else None
        if existing != unit_text:
            SERVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
            SERVICE_PATH.write_text(unit_text, encoding="utf-8")
            os.chmod(SERVICE_PATH, 0o644)
            self._run(["systemctl", "daemon-reload"])
        return unit_text

    def _compute_fingerprint(self, config_yaml: str, unit_text: str, wheel_digest: str) -> str:
        material = json.dumps(
            {
                "config": config_yaml,
                "unit": unit_text,
                "wheel_digest": wheel_digest,
            },
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _is_service_active(self) -> bool:
        result = self._run(["systemctl", "is-active", "--quiet", SERVICE_NAME], check=False)
        return result.returncode == 0

    def _ensure_service_running(self, *, restart: bool) -> None:
        self._run(["systemctl", "enable", SERVICE_NAME], check=False)
        if restart:
            if self._is_service_active():
                self._run(["systemctl", "restart", SERVICE_NAME])
            else:
                self._run(["systemctl", "start", SERVICE_NAME])
            return
        if not self._is_service_active():
            self._run(["systemctl", "start", SERVICE_NAME])

    def _stop_service(self) -> None:
        self._run(["systemctl", "disable", "--now", SERVICE_NAME], check=False)

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
        capture_output: bool = False,
    ) -> sp.CompletedProcess[str]:
        return sp.run(args, check=check, capture_output=capture_output, text=True)

    def _on_run_once_action(self, event: ActionEvent) -> None:
        self._run_once_action(event, dry_run=False)

    def _on_run_once_dry_run_action(self, event: ActionEvent) -> None:
        self._run_once_action(event, dry_run=True)

    def _run_once_action(self, event: ActionEvent, *, dry_run: bool) -> None:
        monitor_bin = VENV_DIR / "bin" / "gcs-release-monitor"
        if not monitor_bin.exists():
            event.fail("monitor binary is not installed")
            return
        if not CONFIG_PATH.exists():
            event.fail(f"config not found: {CONFIG_PATH}")
            return

        cmd = [
            str(monitor_bin),
            "--config",
            str(CONFIG_PATH),
            "--once",
            "--log-level",
            str(self.config.get("log-level", "INFO")),
        ]
        if dry_run:
            cmd.append("--dry-run")
        result = self._run(cmd, check=False, capture_output=True)
        if result.returncode != 0:
            stderr_tail = self._tail_text(str(result.stderr), max_lines=20)
            event.fail(f"gcs-release-monitor failed (exit={result.returncode}): {stderr_tail}")
            return

        event.set_results(
            {
                "exit-code": result.returncode,
                "dry-run": dry_run,
                "stdout": self._tail_text(str(result.stdout), max_lines=20),
            }
        )

    def _on_show_effective_config_action(self, event: ActionEvent) -> None:
        try:
            if CONFIG_PATH.exists():
                raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ReconcileError(f"unexpected config format in {CONFIG_PATH}")
                rendered = raw
            else:
                secrets = self._resolve_secret_bundle()
                webhook = self._resolve_webhook()
                gcs_path = str(GCS_CREDENTIALS_PATH) if secrets.gcs_service_account_json else None
                rendered = _build_render_config(dict(self.config), secrets, webhook, gcs_credentials_file=gcs_path)
        except ReconcileError as exc:
            event.fail(str(exc))
            return

        redacted = self._redact_config(rendered)
        event.set_results(
            {
                "webhook-source": str(self._stored.webhook_source or "unknown"),
                "config": dump_yaml(redacted),
            }
        )

    def _on_service_restart_action(self, event: ActionEvent) -> None:
        try:
            self._ensure_service_running(restart=True)
        except sp.SubprocessError as exc:
            event.fail(f"service restart failed: {exc}")
            return
        event.set_results({"service": SERVICE_NAME, "restarted": True})

    def _redact_config(self, rendered: dict[str, Any]) -> dict[str, Any]:
        cloned = json.loads(json.dumps(rendered))
        if isinstance(cloned.get("nextcloud"), dict):
            if cloned["nextcloud"].get("app_password"):
                cloned["nextcloud"]["app_password"] = "***"
            if cloned["nextcloud"].get("share_password"):
                cloned["nextcloud"]["share_password"] = "***"
        if isinstance(cloned.get("webhook"), dict) and cloned["webhook"].get("shared_secret"):
            cloned["webhook"]["shared_secret"] = "***"
        return cloned

    @staticmethod
    def _tail_text(raw: str, *, max_lines: int) -> str:
        lines = [line for line in raw.strip().splitlines() if line]
        if not lines:
            return ""
        return "\n".join(lines[-max_lines:])


if __name__ == "__main__":  # pragma: nocover
    main(ReleaseMonitorGcloudCharm)
