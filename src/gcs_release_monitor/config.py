from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .types import ARCHIVE_SUFFIX_DEFAULTS, CONTENT_TYPE_DEFAULTS


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class GCSConfig:
    bucket: str
    anonymous: bool
    use_gcloud_cli: bool
    credentials_file: str | None
    include_prefixes: tuple[str, ...]
    include_suffixes: tuple[str, ...]
    include_content_types: tuple[str, ...]


@dataclass(frozen=True)
class NextcloudConfig:
    base_url: str
    username: str
    app_password: str
    remote_dir: str
    verify_tls: bool
    create_public_share: bool
    share_password: str | None
    share_expire_days: int | None
    share_permissions: int


@dataclass(frozen=True)
class WebhookConfig:
    url: str
    shared_secret: str
    timeout_seconds: float
    verify_tls: bool


@dataclass(frozen=True)
class ChainConfig:
    organization: str
    repository: str
    common_name: str
    extra_info: str
    client_name: str | None
    chain_ids: tuple[int, ...]
    genesis_hashes: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseDefaults:
    urgent: bool
    priority: int
    due_date: str


@dataclass(frozen=True)
class ArtifactSelectionRule:
    organization: str | None
    repository: str | None
    binary_patterns: tuple[str, ...]
    genesis_patterns: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactSelectionConfig:
    enabled: bool
    fallback_to_archive: bool
    default_binary_patterns: tuple[str, ...]
    default_genesis_patterns: tuple[str, ...]
    rules: tuple[ArtifactSelectionRule, ...]


@dataclass(frozen=True)
class AppConfig:
    poll_interval_seconds: int
    state_dir: Path
    temp_dir: Path
    gcs: GCSConfig
    nextcloud: NextcloudConfig
    webhook: WebhookConfig
    chain: ChainConfig
    release_defaults: ReleaseDefaults
    artifact_selection: ArtifactSelectionConfig


_REQUIRED_TOP_LEVEL = ("gcs", "nextcloud", "webhook", "chain")


def _required(raw: dict[str, Any], key: str, parent: str = "config") -> Any:
    if key not in raw:
        raise ConfigError(f"Missing required key {key} in {parent}")
    return raw[key]


def _normalize_slash_path(value: str) -> str:
    return "/".join(segment for segment in value.strip("/").split("/") if segment)


def _parse_chain(raw: dict[str, Any]) -> ChainConfig:
    chain_ids_raw = raw.get("chain_ids") or []
    chain_ids: list[int] = []
    for item in chain_ids_raw:
        if isinstance(item, bool):
            raise ConfigError("chain.chain_ids cannot contain booleans")
        chain_ids.append(int(item))

    genesis_raw = raw.get("genesis_hashes") or []
    genesis_hashes = tuple(str(item).lower() for item in genesis_raw)

    if chain_ids and genesis_hashes:
        raise ConfigError("chain.chain_ids and chain.genesis_hashes are mutually exclusive")

    return ChainConfig(
        organization=str(_required(raw, "organization", "chain")),
        repository=str(_required(raw, "repository", "chain")),
        common_name=str(raw.get("common_name") or _required(raw, "repository", "chain")),
        extra_info=str(raw.get("extra_info") or ""),
        client_name=str(raw["client_name"]) if raw.get("client_name") else None,
        chain_ids=tuple(sorted(set(chain_ids))),
        genesis_hashes=tuple(sorted(set(genesis_hashes))),
    )


def _parse_gcs(raw: dict[str, Any]) -> GCSConfig:
    include_prefixes = tuple(str(prefix) for prefix in (raw.get("include_prefixes") or []))
    suffixes = tuple(str(value) for value in (raw.get("include_suffixes") or ARCHIVE_SUFFIX_DEFAULTS))
    content_types = tuple(str(value) for value in (raw.get("include_content_types") or CONTENT_TYPE_DEFAULTS))
    return GCSConfig(
        bucket=str(_required(raw, "bucket", "gcs")),
        anonymous=bool(raw.get("anonymous", False)),
        use_gcloud_cli=bool(raw.get("use_gcloud_cli", False)),
        credentials_file=str(raw["credentials_file"]) if raw.get("credentials_file") else None,
        include_prefixes=include_prefixes,
        include_suffixes=suffixes,
        include_content_types=content_types,
    )


def _parse_nextcloud(raw: dict[str, Any]) -> NextcloudConfig:
    return NextcloudConfig(
        base_url=str(_required(raw, "base_url", "nextcloud")).rstrip("/"),
        username=str(_required(raw, "username", "nextcloud")),
        app_password=str(_required(raw, "app_password", "nextcloud")),
        remote_dir=_normalize_slash_path(str(raw.get("remote_dir") or "release-mirror")),
        verify_tls=bool(raw.get("verify_tls", True)),
        create_public_share=bool(raw.get("create_public_share", True)),
        share_password=str(raw["share_password"]) if raw.get("share_password") else None,
        share_expire_days=int(raw["share_expire_days"]) if raw.get("share_expire_days") else None,
        share_permissions=int(raw.get("share_permissions", 1)),
    )


def _parse_webhook(raw: dict[str, Any]) -> WebhookConfig:
    return WebhookConfig(
        url=str(_required(raw, "url", "webhook")),
        shared_secret=str(_required(raw, "shared_secret", "webhook")),
        timeout_seconds=float(raw.get("timeout_seconds", 10.0)),
        verify_tls=bool(raw.get("verify_tls", True)),
    )


def _parse_release_defaults(raw: dict[str, Any]) -> ReleaseDefaults:
    defaults = raw.get("release_defaults") or {}
    due_date = str(defaults.get("due_date") or "P2D")
    if due_date not in {"P1D", "P2D", "P5D"}:
        raise ConfigError("release_defaults.due_date must be one of P1D, P2D, P5D")
    priority = int(defaults.get("priority", 3))
    if priority not in {1, 3, 4}:
        raise ConfigError("release_defaults.priority must be one of 1, 3, 4")
    return ReleaseDefaults(
        urgent=bool(defaults.get("urgent", False)),
        priority=priority,
        due_date=due_date,
    )


def _parse_artifact_selection(raw: dict[str, Any]) -> ArtifactSelectionConfig:
    section = raw.get("artifact_selection") or {}
    rules_raw = section.get("rules") or []
    rules: list[ArtifactSelectionRule] = []
    for rule_raw in rules_raw:
        if not isinstance(rule_raw, dict):
            raise ConfigError("artifact_selection.rules entries must be mappings")
        binary_patterns = tuple(str(value) for value in (rule_raw.get("binary_patterns") or []))
        genesis_patterns = tuple(str(value) for value in (rule_raw.get("genesis_patterns") or []))
        if not binary_patterns or not genesis_patterns:
            raise ConfigError("artifact_selection rule requires binary_patterns and genesis_patterns")
        rules.append(
            ArtifactSelectionRule(
                organization=str(rule_raw["organization"]) if rule_raw.get("organization") else None,
                repository=str(rule_raw["repository"]) if rule_raw.get("repository") else None,
                binary_patterns=binary_patterns,
                genesis_patterns=genesis_patterns,
            )
        )

    default_binary_patterns = tuple(str(value) for value in (section.get("default_binary_patterns") or []))
    default_genesis_patterns = tuple(str(value) for value in (section.get("default_genesis_patterns") or []))

    return ArtifactSelectionConfig(
        enabled=bool(section.get("enabled", True)),
        fallback_to_archive=bool(section.get("fallback_to_archive", True)),
        default_binary_patterns=default_binary_patterns,
        default_genesis_patterns=default_genesis_patterns,
        rules=tuple(rules),
    )


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if not isinstance(raw, dict):
        raise ConfigError("Top-level config must be a mapping")

    for key in _REQUIRED_TOP_LEVEL:
        _required(raw, key)

    state_dir = Path(str(raw.get("state_dir") or "./state")).expanduser().resolve()
    temp_dir = Path(str(raw.get("temp_dir") or "/tmp/gcs-release-monitor")).expanduser().resolve()

    poll_interval = int(raw.get("poll_interval_seconds", 900))
    if poll_interval < 30:
        raise ConfigError("poll_interval_seconds must be >= 30")

    return AppConfig(
        poll_interval_seconds=poll_interval,
        state_dir=state_dir,
        temp_dir=temp_dir,
        gcs=_parse_gcs(raw["gcs"]),
        nextcloud=_parse_nextcloud(raw["nextcloud"]),
        webhook=_parse_webhook(raw["webhook"]),
        chain=_parse_chain(raw["chain"]),
        release_defaults=_parse_release_defaults(raw),
        artifact_selection=_parse_artifact_selection(raw),
    )
