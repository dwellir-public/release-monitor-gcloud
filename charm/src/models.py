from __future__ import annotations

from dataclasses import dataclass


class ReconcileError(ValueError):
    def __init__(self, message: str, *, stop_service: bool = True):
        super().__init__(message)
        self.stop_service = stop_service


@dataclass(frozen=True)
class SecretBundle:
    nextcloud_username: str | None
    nextcloud_app_password: str | None
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
