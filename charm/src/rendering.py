from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Any

import yaml

import constants as c
from models import ReconcileError, SecretBundle, WebhookResolution


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


def build_render_config(
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

    return {
        "poll_interval_seconds": int(config.get("poll-interval-seconds", 900)),
        "state_dir": str(config.get("state-dir", str(c.STATE_DIR))),
        "temp_dir": str(config.get("temp-dir", str(c.TEMP_DIR))),
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


def render_service_unit(*, log_level: str) -> str:
    template = Template(
        (Path(__file__).resolve().parent.parent / "templates" / "release-monitor-gcloud.service.tmpl").read_text(
            encoding="utf-8"
        )
    )
    exec_start = (
        f"{c.VENV_DIR}/bin/gcs-release-monitor --config {c.CONFIG_PATH} --log-level {log_level}"
    )
    return template.substitute(exec_start=exec_start)


def dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False)


def redact_config(rendered: dict[str, Any]) -> dict[str, Any]:
    cloned = json.loads(json.dumps(rendered))
    if isinstance(cloned.get("nextcloud"), dict):
        if cloned["nextcloud"].get("app_password"):
            cloned["nextcloud"]["app_password"] = "***"
        if cloned["nextcloud"].get("share_password"):
            cloned["nextcloud"]["share_password"] = "***"
    if isinstance(cloned.get("webhook"), dict) and cloned["webhook"].get("shared_secret"):
        cloned["webhook"]["shared_secret"] = "***"
    return cloned


def tail_text(raw: str, *, max_lines: int) -> str:
    lines = [line for line in raw.strip().splitlines() if line]
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])
