from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import NextcloudConfig

logger = logging.getLogger(__name__)


class NextcloudError(RuntimeError):
    pass


class NextcloudClient:
    def __init__(self, config: NextcloudConfig):
        self.config = config
        self._client = httpx.Client(
            auth=(config.username, config.app_password),
            timeout=60.0,
            verify=config.verify_tls,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def upload_file(self, local_path: Path, remote_path: str) -> str:
        self._ensure_directories(remote_path)
        webdav_url = self._webdav_url(remote_path)
        with local_path.open("rb") as handle:
            response = self._client.put(webdav_url, content=handle)
        if response.status_code not in {200, 201, 204}:
            raise NextcloudError(
                f"Nextcloud upload failed with status={response.status_code}: {response.text[:500]}"
            )
        return webdav_url

    def create_public_share(self, remote_path: str) -> str:
        payload: dict[str, str | int] = {
            "path": f"/{remote_path}",
            "shareType": 3,
            "permissions": self.config.share_permissions,
        }
        if self.config.share_password:
            payload["password"] = self.config.share_password
        if self.config.share_expire_days:
            expires = dt.date.today() + dt.timedelta(days=self.config.share_expire_days)
            payload["expireDate"] = expires.isoformat()

        response = self._client.post(
            f"{self.config.base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares",
            data=payload,
            headers={
                "OCS-APIRequest": "true",
                "Accept": "application/json",
            },
        )
        if response.status_code not in {200, 201}:
            raise NextcloudError(
                f"Nextcloud share creation failed with status={response.status_code}: {response.text[:500]}"
            )

        data = response.json()
        share_url = data.get("ocs", {}).get("data", {}).get("url")
        if not share_url:
            raise NextcloudError("Nextcloud share response missing ocs.data.url")
        return str(share_url)

    def _ensure_directories(self, remote_path: str) -> None:
        segments = remote_path.split("/")[:-1]
        if not segments:
            return
        cumulative: list[str] = []
        for segment in segments:
            cumulative.append(segment)
            path = "/".join(cumulative)
            url = self._webdav_url(path)
            response = self._client.request("MKCOL", url)
            if response.status_code in {201, 405}:
                continue
            if response.status_code == 409:
                raise NextcloudError(f"Nextcloud parent folder missing when creating '{path}'")
            raise NextcloudError(
                f"Nextcloud MKCOL failed for {path} with status={response.status_code}: {response.text[:300]}"
            )

    def _webdav_url(self, remote_path: str) -> str:
        encoded = "/".join(quote(part, safe="") for part in remote_path.split("/") if part)
        user = quote(self.config.username, safe="")
        return f"{self.config.base_url}/remote.php/dav/files/{user}/{encoded}"
