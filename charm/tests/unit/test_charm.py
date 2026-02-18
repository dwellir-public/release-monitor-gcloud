from __future__ import annotations

import subprocess as sp
from pathlib import Path
from typing import Any

import pytest
import yaml
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Context, Relation, Resource, Secret, State

import constants as constants_module
import release_monitor_gcloud as runtime_module
from charm import (
    APP_DIR,
    CONFIG_PATH,
    GCS_CREDENTIALS_PATH,
    ReconcileError,
    ReleaseMonitorGcloudCharm,
    SecretBundle,
    WebhookResolution,
    _build_render_config,
    parse_json_array_option,
)


class FakeRunner:
    def __init__(self, *, venv_dir: Path):
        self.commands: list[list[str]] = []
        self._service_active = False
        self._venv_dir = venv_dir

    def __call__(
        self,
        _charm: ReleaseMonitorGcloudCharm,
        args: list[str],
        *,
        check: bool = True,
        capture_output: bool = False,
    ) -> sp.CompletedProcess[str]:
        del capture_output
        self.commands.append(args)

        rc = 0
        stdout = ""
        stderr = ""

        if args[:3] == ["getent", "group", "release-monitor"]:
            rc = 1
        elif args[:3] == ["id", "-u", "release-monitor"]:
            rc = 1
        elif args[:3] == ["python3", "-m", "venv"]:
            (self._venv_dir / "bin").mkdir(parents=True, exist_ok=True)
            (self._venv_dir / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
            (self._venv_dir / "bin" / "pip").write_text("#!/bin/sh\n", encoding="utf-8")
        elif len(args) >= 3 and args[1:3] == ["install", "--upgrade"]:
            (self._venv_dir / "bin").mkdir(parents=True, exist_ok=True)
            (self._venv_dir / "bin" / "gcs-release-monitor").write_text(
                "#!/bin/sh\n", encoding="utf-8"
            )
        elif len(args) >= 3 and args[1] == "-c" and "importlib.metadata" in args[2]:
            stdout = "0.1.0\n"
        elif len(args) >= 3 and args[1] == "-c" and "load_config" in args[2]:
            rc = 0
        elif args[:3] == ["systemctl", "is-active", "--quiet"]:
            rc = 0 if self._service_active else 3
        elif args[:2] == ["systemctl", "start"]:
            self._service_active = True
        elif args[:2] == ["systemctl", "restart"]:
            self._service_active = True
        elif args[:3] == ["systemctl", "disable", "--now"]:
            self._service_active = False
        elif args[:3] == ["systemctl", "enable", "release-monitor-gcloud.service"]:
            rc = 0

        if check and rc != 0:
            raise sp.CalledProcessError(rc, args, output=stdout, stderr=stderr)
        return sp.CompletedProcess(args=args, returncode=rc, stdout=stdout, stderr=stderr)


@pytest.fixture()
def ctx() -> Context:
    return Context(ReleaseMonitorGcloudCharm, charm_root=Path("."))


@pytest.fixture()
def base_config() -> dict[str, Any]:
    return {
        "gcs-bucket": "bucket-a",
        "nextcloud-base-url": "https://cloud.example",
        "nextcloud-remote-dir": "release-mirror",
        "chain-organization": "dwellir",
        "chain-repository": "megaeth",
        "chain-ids": "[]",
        "chain-genesis-hashes": "[]",
        "gcs-include-prefixes": "[]",
        "gcs-include-suffixes": "[]",
        "gcs-include-content-types": "[]",
        "artifact-selection-default-binary-patterns": "[]",
        "artifact-selection-default-genesis-patterns": "[]",
        "artifact-selection-rules": "[]",
        "nextcloud-credentials-secret-id": "secret:nextcloud",
        "gcs-service-account-secret-id": "secret:gcs",
        "webhook-url": "https://fallback.example/v1/releases",
        "webhook-shared-secret-secret-id": "secret:webhook-fallback",
        "log-level": "INFO",
    }


@pytest.fixture()
def base_secrets() -> list[Secret]:
    return [
        Secret(
            {"username": "jonathan", "app-password": "apppass", "share-password": "sharepass"},
            id="secret:nextcloud",
        ),
        Secret(
            {"service-account-json": '{"type":"service_account"}'},
            id="secret:gcs",
        ),
        Secret({"shared-secret": "fallback-shared-secret"}, id="secret:webhook-fallback"),
    ]


@pytest.fixture()
def patched_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    app_dir = tmp_path / "var" / "lib" / "release-monitor-gcloud"
    state_dir = app_dir / "state"
    temp_dir = app_dir / "tmp"
    secrets_dir = app_dir / "secrets"
    etc_dir = tmp_path / "etc" / "release-monitor-gcloud"
    service_path = tmp_path / "etc" / "systemd" / "system" / "release-monitor-gcloud.service"
    venv_dir = tmp_path / "opt" / "release-monitor-gcloud" / "venv"

    monkeypatch.setattr(constants_module, "APP_DIR", app_dir)
    monkeypatch.setattr(constants_module, "STATE_DIR", state_dir)
    monkeypatch.setattr(constants_module, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(constants_module, "SECRETS_DIR", secrets_dir)
    monkeypatch.setattr(constants_module, "ETC_DIR", etc_dir)
    monkeypatch.setattr(constants_module, "CONFIG_PATH", etc_dir / "config.yaml")
    monkeypatch.setattr(constants_module, "SERVICE_PATH", service_path)
    monkeypatch.setattr(constants_module, "VENV_DIR", venv_dir)
    monkeypatch.setattr(
        constants_module, "GCS_CREDENTIALS_PATH", secrets_dir / "gcs-service-account.json"
    )
    monkeypatch.setattr(runtime_module.shutil, "chown", lambda *_a, **_k: None)

    return {
        "app_dir": app_dir,
        "state_dir": state_dir,
        "temp_dir": temp_dir,
        "secrets_dir": secrets_dir,
        "etc_dir": etc_dir,
        "config_path": etc_dir / "config.yaml",
        "service_path": service_path,
        "venv_dir": venv_dir,
        "wheel_path": tmp_path / "gcs_release_monitor-0.1.0-py3-none-any.whl",
    }


def _state(
    *,
    config: dict[str, Any],
    secrets: list[Secret],
    wheel_path: Path | None,
    relation: Relation | None = None,
    planned_units: int = 1,
) -> State:
    resources: list[Resource] = []
    relations: list[Relation] = []
    if wheel_path is not None:
        resources.append(Resource(name="release-monitor-wheel", path=wheel_path))
    if relation is not None:
        relations.append(relation)
    return State(
        config=config,
        secrets=secrets,
        resources=resources,
        relations=relations,
        planned_units=planned_units,
    )


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> None:
    def _run(
        self: ReleaseMonitorGcloudCharm,
        args: list[str],
        *,
        check: bool = True,
        capture_output: bool = False,
    ) -> sp.CompletedProcess[str]:
        return runner(self, args, check=check, capture_output=capture_output)

    monkeypatch.setattr(ReleaseMonitorGcloudCharm, "_run", _run)


def test_parse_json_array_option_rejects_non_array():
    with pytest.raises(ReconcileError, match="must be a JSON array"):
        parse_json_array_option('{"k": 1}', "x")


def test_build_render_config_maps_charm_keys_to_app_schema():
    cfg = {
        "poll-interval-seconds": 123,
        "state-dir": "/var/lib/release-monitor-gcloud/state",
        "temp-dir": "/var/lib/release-monitor-gcloud/tmp",
        "gcs-bucket": "bucket-a",
        "gcs-include-prefixes": '["rpc/"]',
        "gcs-include-suffixes": '[".tar.gz"]',
        "gcs-include-content-types": '["application/gzip"]',
        "nextcloud-base-url": "https://cloud.example",
        "nextcloud-remote-dir": "releases",
        "chain-organization": "org",
        "chain-repository": "repo",
        "chain-ids": "[]",
        "chain-genesis-hashes": "[]",
        "artifact-selection-default-binary-patterns": '["rpc-node-*"]',
        "artifact-selection-default-genesis-patterns": '["mainnet/genesis.json"]',
        "artifact-selection-rules": "[]",
    }
    secrets = SecretBundle(
        nextcloud_username="u",
        nextcloud_app_password="p",
        nextcloud_share_password=None,
        gcs_service_account_json=None,
    )
    webhook = WebhookResolution(url="https://hook", shared_secret="secret", source="relation")

    rendered = _build_render_config(cfg, secrets, webhook, gcs_credentials_file=None)

    assert rendered["delivery_mode"] == "full"
    assert rendered["poll_interval_seconds"] == 123
    assert rendered["gcs"]["bucket"] == "bucket-a"
    assert rendered["nextcloud"]["base_url"] == "https://cloud.example"
    assert rendered["chain"]["organization"] == "org"
    assert rendered["release_defaults"]["due_date"] == "P2D"
    assert rendered["artifact_selection"]["default_binary_patterns"] == ["rpc-node-*"]


def test_build_render_config_webhook_only_omits_nextcloud_section():
    cfg = {
        "delivery-mode": "webhook_only",
        "gcs-bucket": "bucket-a",
        "chain-organization": "org",
        "chain-repository": "repo",
        "chain-ids": "[]",
        "chain-genesis-hashes": "[]",
        "gcs-include-prefixes": "[]",
        "gcs-include-suffixes": "[]",
        "gcs-include-content-types": "[]",
        "artifact-selection-default-binary-patterns": "[]",
        "artifact-selection-default-genesis-patterns": "[]",
        "artifact-selection-rules": "[]",
    }
    secrets = SecretBundle(
        nextcloud_username=None,
        nextcloud_app_password=None,
        nextcloud_share_password=None,
        gcs_service_account_json=None,
    )
    webhook = WebhookResolution(url="https://hook", shared_secret="secret", source="relation")

    rendered = _build_render_config(cfg, secrets, webhook, gcs_credentials_file=None)

    assert rendered["delivery_mode"] == "webhook_only"
    assert "nextcloud" not in rendered


def test_install_event_creates_runtime_dirs_and_unit_file(
    ctx: Context,
    base_config: dict[str, Any],
    base_secrets: list[Secret],
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    patched_paths["wheel_path"].write_bytes(b"wheel")
    runner = FakeRunner(venv_dir=patched_paths["venv_dir"])
    _patch_runner(monkeypatch, runner)

    state = _state(
        config=base_config,
        secrets=base_secrets,
        wheel_path=patched_paths["wheel_path"],
    )
    out = ctx.run(ctx.on.install(), state)

    assert isinstance(out.unit_status, ActiveStatus)
    assert patched_paths["state_dir"].exists()
    assert patched_paths["temp_dir"].exists()
    assert patched_paths["secrets_dir"].exists()
    assert patched_paths["config_path"].exists()
    assert patched_paths["service_path"].exists()

    assert any(cmd[:3] == ["python3", "-m", "venv"] for cmd in runner.commands)
    assert any(cmd[:2] == ["groupadd", "--system"] for cmd in runner.commands)
    assert any(cmd[:2] == ["systemctl", "daemon-reload"] for cmd in runner.commands)


def test_missing_release_monitor_wheel_blocks(
    ctx: Context,
    base_config: dict[str, Any],
    base_secrets: list[Secret],
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    runner = FakeRunner(venv_dir=patched_paths["venv_dir"])
    _patch_runner(monkeypatch, runner)

    state = _state(config=base_config, secrets=base_secrets, wheel_path=None)
    out = ctx.run(ctx.on.install(), state)

    assert isinstance(out.unit_status, BlockedStatus)
    assert "missing required resource" in out.unit_status.message
    assert not any(cmd[:3] == ["systemctl", "disable", "--now"] for cmd in runner.commands)


def test_missing_required_secret_field_blocks(
    ctx: Context,
    base_config: dict[str, Any],
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    patched_paths["wheel_path"].write_bytes(b"wheel")
    runner = FakeRunner(venv_dir=patched_paths["venv_dir"])
    _patch_runner(monkeypatch, runner)

    bad_secrets = [
        Secret({"username": "jonathan"}, id="secret:nextcloud"),
        Secret({"service-account-json": "{}"}, id="secret:gcs"),
        Secret({"shared-secret": "fallback-shared-secret"}, id="secret:webhook-fallback"),
    ]

    state = _state(config=base_config, secrets=bad_secrets, wheel_path=patched_paths["wheel_path"])
    out = ctx.run(ctx.on.config_changed(), state)

    assert isinstance(out.unit_status, BlockedStatus)
    assert "app-password" in out.unit_status.message


def test_nextcloud_underscore_secret_key_is_rejected(
    ctx: Context,
    base_config: dict[str, Any],
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    patched_paths["wheel_path"].write_bytes(b"wheel")
    runner = FakeRunner(venv_dir=patched_paths["venv_dir"])
    _patch_runner(monkeypatch, runner)

    bad_secrets = [
        Secret({"username": "jonathan", "app_password": "apppass"}, id="secret:nextcloud"),
        Secret({"service-account-json": "{}"}, id="secret:gcs"),
        Secret({"shared-secret": "fallback-shared-secret"}, id="secret:webhook-fallback"),
    ]

    state = _state(config=base_config, secrets=bad_secrets, wheel_path=patched_paths["wheel_path"])
    out = ctx.run(ctx.on.config_changed(), state)

    assert isinstance(out.unit_status, BlockedStatus)
    assert "app-password" in out.unit_status.message


def test_gcs_underscore_secret_key_is_rejected(
    ctx: Context,
    base_config: dict[str, Any],
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    patched_paths["wheel_path"].write_bytes(b"wheel")
    runner = FakeRunner(venv_dir=patched_paths["venv_dir"])
    _patch_runner(monkeypatch, runner)

    bad_secrets = [
        Secret({"username": "jonathan", "app-password": "apppass"}, id="secret:nextcloud"),
        Secret({"service_account_json": "{}"}, id="secret:gcs"),
        Secret({"shared-secret": "fallback-shared-secret"}, id="secret:webhook-fallback"),
    ]

    state = _state(config=base_config, secrets=bad_secrets, wheel_path=patched_paths["wheel_path"])
    out = ctx.run(ctx.on.config_changed(), state)

    assert isinstance(out.unit_status, BlockedStatus)
    assert "service-account-json" in out.unit_status.message


def test_relation_secret_id_precedence_over_fallback(
    ctx: Context,
    base_config: dict[str, Any],
    base_secrets: list[Secret],
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    patched_paths["wheel_path"].write_bytes(b"wheel")
    runner = FakeRunner(venv_dir=patched_paths["venv_dir"])
    _patch_runner(monkeypatch, runner)

    relation = Relation(
        endpoint="release-monitor-webhook",
        interface="release-monitor-webhook",
        remote_app_name="release-filter",
        remote_app_data={
            "protocol_version": "release-monitor-webhook.v1",
            "webhook_url": "https://relation.example/v1/releases",
            "secret_id": "secret:relation",
            "webhook_secret": "plaintext-should-not-win",
        },
    )

    secrets = list(base_secrets) + [
        Secret({"shared-secret": "relation-secret"}, id="secret:relation")
    ]
    state = _state(
        config=base_config,
        secrets=secrets,
        wheel_path=patched_paths["wheel_path"],
        relation=relation,
    )
    out = ctx.run(ctx.on.install(), state)

    assert isinstance(out.unit_status, ActiveStatus)
    assert "relation-secret-id" in out.unit_status.message
    rendered = yaml.safe_load(patched_paths["config_path"].read_text(encoding="utf-8"))
    assert rendered["webhook"]["url"] == "https://relation.example/v1/releases"
    assert rendered["webhook"]["shared_secret"] == "relation-secret"


def test_relation_broken_uses_config_fallback(
    ctx: Context,
    base_config: dict[str, Any],
    base_secrets: list[Secret],
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    patched_paths["wheel_path"].write_bytes(b"wheel")
    runner = FakeRunner(venv_dir=patched_paths["venv_dir"])
    _patch_runner(monkeypatch, runner)

    state = _state(
        config=base_config,
        secrets=base_secrets,
        wheel_path=patched_paths["wheel_path"],
    )
    out = ctx.run(ctx.on.config_changed(), state)

    assert isinstance(out.unit_status, ActiveStatus)
    assert "config-fallback" in out.unit_status.message
    rendered = yaml.safe_load(patched_paths["config_path"].read_text(encoding="utf-8"))
    assert rendered["webhook"]["url"] == "https://fallback.example/v1/releases"
    assert rendered["webhook"]["shared_secret"] == "fallback-shared-secret"


def test_invalid_candidate_config_keeps_last_known_good(
    ctx: Context,
    base_config: dict[str, Any],
    base_secrets: list[Secret],
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    patched_paths["wheel_path"].write_bytes(b"wheel")
    runner = FakeRunner(venv_dir=patched_paths["venv_dir"])
    _patch_runner(monkeypatch, runner)

    state = _state(
        config=base_config,
        secrets=base_secrets,
        wheel_path=patched_paths["wheel_path"],
    )
    ctx.run(ctx.on.install(), state)
    previous = patched_paths["config_path"].read_text(encoding="utf-8")

    def fail_validate(_self: ReleaseMonitorGcloudCharm, _path: Path) -> None:
        raise ReconcileError("invalid rendered config: test", stop_service=False)

    monkeypatch.setattr(ReleaseMonitorGcloudCharm, "_validate_candidate_config", fail_validate)

    bad_config = dict(base_config)
    bad_config["release-defaults-due-date"] = "P99D"
    out = ctx.run(
        ctx.on.config_changed(),
        _state(config=bad_config, secrets=base_secrets, wheel_path=patched_paths["wheel_path"]),
    )

    assert isinstance(out.unit_status, BlockedStatus)
    assert "invalid rendered config" in out.unit_status.message
    current = patched_paths["config_path"].read_text(encoding="utf-8")
    assert current == previous


def test_single_unit_guard_blocks_scale_greater_than_one(
    ctx: Context,
    base_config: dict[str, Any],
    base_secrets: list[Secret],
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    patched_paths["wheel_path"].write_bytes(b"wheel")
    runner = FakeRunner(venv_dir=patched_paths["venv_dir"])
    _patch_runner(monkeypatch, runner)

    out = ctx.run(
        ctx.on.install(),
        _state(
            config=base_config,
            secrets=base_secrets,
            wheel_path=patched_paths["wheel_path"],
            planned_units=2,
        ),
    )

    assert isinstance(out.unit_status, BlockedStatus)
    assert "single-unit charm; scale to 1" in out.unit_status.message
    assert any(cmd[:3] == ["systemctl", "disable", "--now"] for cmd in runner.commands)


def test_webhook_only_mode_allows_missing_nextcloud_secret(
    ctx: Context,
    base_config: dict[str, Any],
    base_secrets: list[Secret],
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    patched_paths["wheel_path"].write_bytes(b"wheel")
    runner = FakeRunner(venv_dir=patched_paths["venv_dir"])
    _patch_runner(monkeypatch, runner)

    config = dict(base_config)
    config["delivery-mode"] = "webhook_only"
    config["nextcloud-credentials-secret-id"] = ""
    secrets = [secret for secret in base_secrets if secret.id != "secret:nextcloud"]

    out = ctx.run(
        ctx.on.config_changed(),
        _state(config=config, secrets=secrets, wheel_path=patched_paths["wheel_path"]),
    )

    assert isinstance(out.unit_status, ActiveStatus)
    rendered = yaml.safe_load(patched_paths["config_path"].read_text(encoding="utf-8"))
    assert rendered["delivery_mode"] == "webhook_only"
    assert "nextcloud" not in rendered


def test_invalid_delivery_mode_blocks(
    ctx: Context,
    base_config: dict[str, Any],
    base_secrets: list[Secret],
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    patched_paths["wheel_path"].write_bytes(b"wheel")
    runner = FakeRunner(venv_dir=patched_paths["venv_dir"])
    _patch_runner(monkeypatch, runner)

    config = dict(base_config)
    config["delivery-mode"] = "invalid"
    out = ctx.run(
        ctx.on.config_changed(),
        _state(config=config, secrets=base_secrets, wheel_path=patched_paths["wheel_path"]),
    )

    assert isinstance(out.unit_status, BlockedStatus)
    assert "delivery-mode must be one of full, webhook_only" in out.unit_status.message


def test_run_once_and_dry_run_actions_invoke_expected_flags(
    ctx: Context,
    patched_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    (patched_paths["venv_dir"] / "bin").mkdir(parents=True, exist_ok=True)
    (patched_paths["venv_dir"] / "bin" / "gcs-release-monitor").write_text(
        "#!/bin/sh\n", encoding="utf-8"
    )
    patched_paths["config_path"].parent.mkdir(parents=True, exist_ok=True)
    patched_paths["config_path"].write_text("gcs: {}\n", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(
        _self: ReleaseMonitorGcloudCharm,
        args: list[str],
        *,
        check: bool = True,
        capture_output: bool = False,
    ) -> sp.CompletedProcess[str]:
        del check, capture_output
        commands.append(args)
        return sp.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(ReleaseMonitorGcloudCharm, "_run", fake_run)

    state = State(config={"log-level": "DEBUG"})
    ctx.run(ctx.on.action("run-once"), state)
    ctx.run(ctx.on.action("run-once-dry-run"), state)

    assert any("--once" in cmd and "--dry-run" not in cmd for cmd in commands)
    assert any("--once" in cmd and "--dry-run" in cmd for cmd in commands)


def test_gcs_credentials_path_constant_points_inside_app_dir():
    assert str(GCS_CREDENTIALS_PATH).startswith(str(APP_DIR))
    assert str(CONFIG_PATH).endswith("config.yaml")
