from __future__ import annotations

from pathlib import Path

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
