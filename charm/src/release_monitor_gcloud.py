from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml
from ops.charm import ActionEvent
from ops.model import ActiveStatus, ModelError, SecretNotFoundError

import constants as c
from models import ReconcileError, SecretBundle, WheelInstall
from release_filter_webhook_requirer import ReleaseFilterWebhookRequirer
from rendering import build_render_config, dump_yaml, redact_config, render_service_unit, tail_text


class ReleaseMonitorRuntime:
    def __init__(self, charm: Any):
        self._charm = charm

    def reconcile(self) -> None:
        if int(self._charm.app.planned_units()) > 1:
            raise ReconcileError("single-unit charm; scale to 1")

        secrets = self._resolve_secret_bundle()
        webhook = ReleaseFilterWebhookRequirer(
            relations=list(self._charm.model.relations.get(c.RELATION_NAME, [])),
            read_secret_content=self._read_secret_content,
        ).resolve(dict(self._charm.config))

        self._ensure_service_user_group()
        self._ensure_runtime_dirs()
        wheel = self._ensure_venv_and_wheel()

        gcs_credentials_file = self._write_gcs_credentials_if_needed(secrets)
        config_map = build_render_config(
            dict(self._charm.config),
            secrets,
            webhook,
            gcs_credentials_file=gcs_credentials_file,
        )

        candidate_path = Path(f"{c.CONFIG_PATH}.new")
        config_yaml = dump_yaml(config_map)
        candidate_path.write_text(config_yaml, encoding="utf-8")
        os.chmod(candidate_path, 0o640)

        self._charm._validate_candidate_config(candidate_path)
        os.replace(candidate_path, c.CONFIG_PATH)

        unit_text = self._install_systemd_unit(str(self._charm.config.get("log-level", "INFO")))

        fingerprint = self._compute_fingerprint(config_yaml, unit_text, wheel.digest)
        restart_required = fingerprint != str(self._charm._stored.config_fingerprint)

        self._ensure_service_running(restart=restart_required)
        if not self._is_service_active():
            raise ReconcileError("service failed to start", stop_service=False)

        self._charm._stored.config_fingerprint = fingerprint
        self._charm._stored.wheel_digest = wheel.digest
        self._charm._stored.wheel_version = wheel.version
        self._charm._stored.webhook_source = webhook.source
        self._charm.unit.status = ActiveStatus(f"service active (webhook: {webhook.source})")

    def stop_service(self) -> None:
        self._charm._run(["systemctl", "disable", "--now", c.SERVICE_NAME], check=False)

    def run_once_action(self, event: ActionEvent, *, dry_run: bool) -> None:
        monitor_bin = c.VENV_DIR / "bin" / "gcs-release-monitor"
        if not monitor_bin.exists():
            event.fail("monitor binary is not installed")
            return
        if not c.CONFIG_PATH.exists():
            event.fail(f"config not found: {c.CONFIG_PATH}")
            return

        cmd = [
            str(monitor_bin),
            "--config",
            str(c.CONFIG_PATH),
            "--once",
            "--log-level",
            str(self._charm.config.get("log-level", "INFO")),
        ]
        if dry_run:
            cmd.append("--dry-run")
        result = self._charm._run(cmd, check=False, capture_output=True)
        if result.returncode != 0:
            stderr_tail = tail_text(str(result.stderr), max_lines=20)
            event.fail(f"gcs-release-monitor failed (exit={result.returncode}): {stderr_tail}")
            return

        event.set_results(
            {
                "exit-code": result.returncode,
                "dry-run": dry_run,
                "stdout": tail_text(str(result.stdout), max_lines=20),
            }
        )

    def show_effective_config_action(self, event: ActionEvent) -> None:
        try:
            if c.CONFIG_PATH.exists():
                raw = yaml.safe_load(c.CONFIG_PATH.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ReconcileError(f"unexpected config format in {c.CONFIG_PATH}")
                rendered = raw
            else:
                secrets = self._resolve_secret_bundle()
                webhook = ReleaseFilterWebhookRequirer(
                    relations=list(self._charm.model.relations.get(c.RELATION_NAME, [])),
                    read_secret_content=self._read_secret_content,
                ).resolve(dict(self._charm.config))
                gcs_path = str(c.GCS_CREDENTIALS_PATH) if secrets.gcs_service_account_json else None
                rendered = build_render_config(
                    dict(self._charm.config),
                    secrets,
                    webhook,
                    gcs_credentials_file=gcs_path,
                )
        except ReconcileError as exc:
            event.fail(str(exc))
            return

        event.set_results(
            {
                "webhook-source": str(self._charm._stored.webhook_source or "unknown"),
                "config": dump_yaml(redact_config(rendered)),
            }
        )

    def service_restart_action(self, event: ActionEvent) -> None:
        try:
            self._ensure_service_running(restart=True)
        except Exception as exc:
            event.fail(f"service restart failed: {exc}")
            return
        event.set_results({"service": c.SERVICE_NAME, "restarted": True})

    def validate_candidate_config(self, candidate_path: Path) -> None:
        python_bin = c.VENV_DIR / "bin" / "python"
        command = (
            "from gcs_release_monitor.config import load_config; "
            f"load_config(r'{candidate_path}')"
        )
        result = self._charm._run([str(python_bin), "-c", command], capture_output=True, check=False)
        if result.returncode != 0:
            message = str(result.stderr).strip() or str(result.stdout).strip() or "validation failed"
            raise ReconcileError(f"invalid rendered config: {message}", stop_service=False)

    def _resolve_secret_bundle(self) -> SecretBundle:
        config = dict(self._charm.config)
        delivery_mode = str(config.get("delivery-mode", "full")).strip().lower()
        if delivery_mode not in {"full", "webhook_only"}:
            raise ReconcileError("invalid config: delivery-mode must be one of full, webhook_only")

        username: str | None = None
        app_password: str | None = None
        share_password: str | None = None
        if delivery_mode == "full":
            nextcloud_secret_id = str(config.get("nextcloud-credentials-secret-id", "")).strip()
            if not nextcloud_secret_id:
                raise ReconcileError("missing required config: nextcloud-credentials-secret-id")
            nextcloud_content = self._read_secret_content(nextcloud_secret_id)

            username = str(nextcloud_content.get("username", "")).strip()
            app_password = str(nextcloud_content.get("app-password", "")).strip()
            share_password = str(nextcloud_content.get("share-password", "")).strip() or None
            if not username:
                raise ReconcileError("missing required secret field username in nextcloud-credentials")
            if not app_password:
                raise ReconcileError(
                    "missing required secret field app-password in nextcloud-credentials"
                )

        gcs_service_account_json: str | None = None
        anonymous = bool(config.get("gcs-anonymous", False))
        use_gcloud_cli = bool(config.get("gcs-use-gcloud-cli", False))
        gcs_secret_id = str(config.get("gcs-service-account-secret-id", "")).strip()

        if not anonymous and not use_gcloud_cli:
            if not gcs_secret_id:
                raise ReconcileError("missing required config: gcs-service-account-secret-id")
            gcs_content = self._read_secret_content(gcs_secret_id)
            gcs_service_account_json = str(gcs_content.get("service-account-json", "")).strip()
            if not gcs_service_account_json:
                raise ReconcileError(
                    "missing required secret field service-account-json in gcs-service-account"
                )

        return SecretBundle(
            nextcloud_username=username,
            nextcloud_app_password=app_password,
            nextcloud_share_password=share_password,
            gcs_service_account_json=gcs_service_account_json,
        )

    def _read_secret_content(self, secret_id: str) -> dict[str, str]:
        try:
            secret = self._charm.model.get_secret(id=secret_id)
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
        if self._charm._run(["getent", "group", c.APP_GROUP], check=False).returncode != 0:
            self._charm._run(["groupadd", "--system", c.APP_GROUP])

        if self._charm._run(["id", "-u", c.APP_USER], check=False).returncode != 0:
            self._charm._run(
                [
                    "useradd",
                    "--system",
                    "--gid",
                    c.APP_GROUP,
                    "--home-dir",
                    str(c.APP_DIR),
                    "--no-create-home",
                    "--shell",
                    "/usr/sbin/nologin",
                    c.APP_USER,
                ]
            )

    def _ensure_runtime_dirs(self) -> None:
        for path in (c.APP_DIR, c.STATE_DIR, c.TEMP_DIR, c.SECRETS_DIR, c.ETC_DIR):
            path.mkdir(parents=True, exist_ok=True)

        for path in (c.APP_DIR, c.STATE_DIR, c.TEMP_DIR, c.SECRETS_DIR):
            shutil.chown(path, user=c.APP_USER, group=c.APP_GROUP)
            os.chmod(path, 0o750)

    def _ensure_venv_and_wheel(self) -> WheelInstall:
        try:
            wheel_path = Path(self._charm.model.resources.fetch(c.RESOURCE_NAME))
        except (ModelError, RuntimeError) as exc:
            raise ReconcileError(
                f"missing required resource: {c.RESOURCE_NAME}", stop_service=False
            ) from exc

        if not wheel_path.exists() or not wheel_path.is_file():
            raise ReconcileError(f"unreadable wheel resource: {wheel_path}", stop_service=False)

        digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
        python_bin = c.VENV_DIR / "bin" / "python"
        pip_bin = c.VENV_DIR / "bin" / "pip"
        cli_bin = c.VENV_DIR / "bin" / "gcs-release-monitor"

        if not python_bin.exists():
            self._charm._run(["python3", "-m", "venv", str(c.VENV_DIR)])

        install_required = digest != str(self._charm._stored.wheel_digest) or not cli_bin.exists()
        if install_required:
            self._charm._run([str(pip_bin), "install", "--upgrade", "--force-reinstall", str(wheel_path)])

        version = self._installed_package_version(python_bin)
        if not version:
            raise ReconcileError("failed to determine installed gcs-release-monitor version", stop_service=False)

        return WheelInstall(digest=digest, version=version)

    def _installed_package_version(self, python_bin: Path) -> str:
        command = "import importlib.metadata as m; print(m.version('gcs-release-monitor'))"
        result = self._charm._run([str(python_bin), "-c", command], capture_output=True, check=False)
        if result.returncode != 0:
            raise ReconcileError("failed to read installed wheel metadata", stop_service=False)
        return str(result.stdout).strip()

    def _write_gcs_credentials_if_needed(self, secrets: SecretBundle) -> str | None:
        if secrets.gcs_service_account_json is None:
            if c.GCS_CREDENTIALS_PATH.exists():
                c.GCS_CREDENTIALS_PATH.unlink()
            return None

        c.GCS_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        c.GCS_CREDENTIALS_PATH.write_text(secrets.gcs_service_account_json, encoding="utf-8")
        os.chmod(c.GCS_CREDENTIALS_PATH, 0o600)
        shutil.chown(c.GCS_CREDENTIALS_PATH, user=c.APP_USER, group=c.APP_GROUP)
        return str(c.GCS_CREDENTIALS_PATH)

    def _install_systemd_unit(self, log_level: str) -> str:
        unit_text = render_service_unit(log_level=log_level)
        existing = c.SERVICE_PATH.read_text(encoding="utf-8") if c.SERVICE_PATH.exists() else None
        if existing != unit_text:
            c.SERVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
            c.SERVICE_PATH.write_text(unit_text, encoding="utf-8")
            os.chmod(c.SERVICE_PATH, 0o644)
            self._charm._run(["systemctl", "daemon-reload"])
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
        result = self._charm._run(["systemctl", "is-active", "--quiet", c.SERVICE_NAME], check=False)
        return result.returncode == 0

    def _ensure_service_running(self, *, restart: bool) -> None:
        self._charm._run(["systemctl", "enable", c.SERVICE_NAME], check=False)
        if restart:
            if self._is_service_active():
                self._charm._run(["systemctl", "restart", c.SERVICE_NAME])
            else:
                self._charm._run(["systemctl", "start", c.SERVICE_NAME])
            return
        if not self._is_service_active():
            self._charm._run(["systemctl", "start", c.SERVICE_NAME])
