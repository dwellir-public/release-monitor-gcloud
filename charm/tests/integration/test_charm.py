#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from pytest_operator.plugin import OpsTest

import yaml

CHARMCRAFT = yaml.safe_load(Path("./charmcraft.yaml").read_text(encoding="utf-8"))
APP_NAME = CHARMCRAFT["name"]


def _wheel_resource_path() -> Path | None:
    env_value = os.environ.get("RELEASE_MONITOR_WHEEL")
    if env_value:
        candidate = Path(env_value).expanduser().resolve()
        if candidate.exists():
            return candidate
    return None


@pytest.mark.abort_on_fail
async def test_build_and_deploy_blocked_without_required_secrets(ops_test: OpsTest):
    """Smoke integration test.

    This test intentionally deploys without required secrets and verifies the charm reports
    a blocked status with actionable messaging.

    Set RELEASE_MONITOR_WHEEL to run this test.
    """

    wheel_path = _wheel_resource_path()
    if not wheel_path:
        pytest.skip("Set RELEASE_MONITOR_WHEEL to run integration tests")

    charm = await ops_test.build_charm(".")
    await asyncio.gather(
        ops_test.model.deploy(
            charm,
            application_name=APP_NAME,
            resources={"release-monitor-wheel": str(wheel_path)},
            config={
                "gcs-bucket": "placeholder-bucket",
                "nextcloud-base-url": "https://cloud.example",
                "nextcloud-remote-dir": "release-mirror",
                "chain-organization": "dwellir",
                "chain-repository": "release-monitor-gcloud",
                "webhook-url": "https://fallback.example/v1/releases",
            },
        ),
        ops_test.model.wait_for_idle(apps=[APP_NAME], raise_on_error=False, timeout=1200),
    )

    unit = ops_test.model.applications[APP_NAME].units[0]
    assert unit.workload_status == "blocked"
    assert "nextcloud-credentials-secret-id" in unit.workload_status_message
