from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gcs_release_monitor.config import ConfigError, load_config


def _write_config(path: Path, payload: dict) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _base_sections() -> dict:
    return {
        "gcs": {
            "bucket": "bucket-a",
            "include_prefixes": [],
            "include_suffixes": [],
            "include_content_types": [],
        },
        "webhook": {
            "url": "https://release-filter.example/v1/releases",
            "shared_secret": "secret",
        },
        "chain": {
            "organization": "dwellir",
            "repository": "release-monitor-gcloud",
        },
    }


def test_load_config_full_mode_requires_nextcloud(tmp_path: Path) -> None:
    payload = {"delivery_mode": "full", "poll_interval_seconds": 60}
    payload.update(_base_sections())
    config_path = _write_config(tmp_path / "config.yaml", payload)

    with pytest.raises(ConfigError, match="Missing required key nextcloud"):
        load_config(config_path)


def test_load_config_webhook_only_allows_missing_nextcloud(tmp_path: Path) -> None:
    payload = {"delivery_mode": "webhook_only", "poll_interval_seconds": 60}
    payload.update(_base_sections())
    config_path = _write_config(tmp_path / "config.yaml", payload)

    parsed = load_config(config_path)
    assert parsed.delivery_mode == "webhook_only"
    assert parsed.nextcloud is None


def test_load_config_rejects_invalid_delivery_mode(tmp_path: Path) -> None:
    payload = {"delivery_mode": "invalid-mode", "poll_interval_seconds": 60}
    payload.update(_base_sections())
    config_path = _write_config(tmp_path / "config.yaml", payload)

    with pytest.raises(ConfigError, match="delivery_mode must be one of"):
        load_config(config_path)
