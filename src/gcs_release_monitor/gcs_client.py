from __future__ import annotations

import logging
import subprocess
import json
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import httpx
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import storage

from .config import GCSConfig
from .types import ObjectMetadata, Snapshot, now_iso

logger = logging.getLogger(__name__)


class GCSClient:
    def __init__(self, config: GCSConfig):
        self.config = config
        self._http = httpx.Client(timeout=60.0)
        self.client: storage.Client | None = None
        self.use_gcloud_cli = config.use_gcloud_cli
        if config.credentials_file:
            self.client = storage.Client.from_service_account_json(config.credentials_file)
        elif not config.anonymous and not self.use_gcloud_cli:
            try:
                self.client = storage.Client()
            except DefaultCredentialsError:
                self.use_gcloud_cli = True
                logger.warning(
                    "ADC not configured; falling back to gcloud CLI for bucket '%s'.",
                    config.bucket,
                )

    def close(self) -> None:
        self._http.close()

    def list_snapshot(self) -> Snapshot:
        objects: dict[str, ObjectMetadata] = {}
        if self.use_gcloud_cli:
            for item in self._iter_objects_gcloud():
                metadata = ObjectMetadata(
                    bucket=self.config.bucket,
                    name=str(item.get("name", "")),
                    size=int(item.get("size") or 0),
                    content_type=item.get("contentType"),
                    generation=str(item.get("generation") or ""),
                    metageneration=str(item.get("metageneration")) if item.get("metageneration") else None,
                    md5_hash=item.get("md5Hash"),
                    crc32c=item.get("crc32c"),
                    etag=item.get("etag"),
                    updated=str(item.get("updated") or ""),
                    time_created=str(item.get("timeCreated")) if item.get("timeCreated") else None,
                )
                if metadata.object_id:
                    objects[metadata.object_id] = metadata
        elif self.client is None:
            for item in self._iter_objects_anonymous():
                metadata = ObjectMetadata(
                    bucket=self.config.bucket,
                    name=str(item.get("name", "")),
                    size=int(item.get("size") or 0),
                    content_type=item.get("contentType"),
                    generation=str(item.get("generation") or ""),
                    metageneration=str(item.get("metageneration")) if item.get("metageneration") else None,
                    md5_hash=item.get("md5Hash"),
                    crc32c=item.get("crc32c"),
                    etag=item.get("etag"),
                    updated=str(item.get("updated") or ""),
                    time_created=str(item.get("timeCreated")) if item.get("timeCreated") else None,
                )
                if metadata.object_id:
                    objects[metadata.object_id] = metadata
        else:
            for blob in self._iter_blobs():
                metadata = ObjectMetadata(
                    bucket=self.config.bucket,
                    name=blob.name,
                    size=int(blob.size or 0),
                    content_type=blob.content_type,
                    generation=str(blob.generation or ""),
                    metageneration=str(blob.metageneration) if blob.metageneration is not None else None,
                    md5_hash=blob.md5_hash,
                    crc32c=blob.crc32c,
                    etag=blob.etag,
                    updated=blob.updated.isoformat() if blob.updated else "",
                    time_created=blob.time_created.isoformat() if blob.time_created else None,
                )
                if metadata.object_id:
                    objects[metadata.object_id] = metadata
        return Snapshot(bucket=self.config.bucket, captured_at=now_iso(), objects=objects)

    def download_object(self, object_name: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.use_gcloud_cli:
            source = f"gs://{self.config.bucket}/{object_name}"
            subprocess.run(["gcloud", "storage", "cp", source, str(destination)], check=True)
            return
        if self.client is None:
            encoded = quote(object_name, safe="/")
            url = f"https://storage.googleapis.com/{self.config.bucket}/{encoded}"
            response = self._http.get(url)
            response.raise_for_status()
            destination.write_bytes(response.content)
            return
        bucket = self.client.bucket(self.config.bucket)
        blob = bucket.blob(object_name)
        blob.download_to_filename(str(destination))

    def _iter_blobs(self) -> Iterable[storage.Blob]:
        if self.client is None:
            return
        if self.config.include_prefixes:
            seen: set[str] = set()
            for prefix in self.config.include_prefixes:
                for blob in self.client.list_blobs(self.config.bucket, prefix=prefix):
                    if blob.name in seen:
                        continue
                    seen.add(blob.name)
                    yield blob
            return
        for blob in self.client.list_blobs(self.config.bucket):
            yield blob

    def _iter_objects_anonymous(self) -> Iterable[dict]:
        prefixes = self.config.include_prefixes or ("",)
        seen: set[str] = set()
        for prefix in prefixes:
            page_token = None
            while True:
                params = {
                    "projection": "noAcl",
                    "maxResults": 1000,
                }
                if prefix:
                    params["prefix"] = prefix
                if page_token:
                    params["pageToken"] = page_token
                response = self._http.get(
                    f"https://storage.googleapis.com/storage/v1/b/{self.config.bucket}/o",
                    params=params,
                )
                if response.status_code in {401, 403}:
                    raise RuntimeError(
                        f"Anonymous listing denied for bucket '{self.config.bucket}'. "
                        "Use authenticated mode (anonymous=false) with ADC or credentials_file."
                    )
                response.raise_for_status()
                payload = response.json()
                for item in payload.get("items", []):
                    name = item.get("name")
                    generation = item.get("generation")
                    key = f"{name}#{generation}"
                    if key in seen:
                        continue
                    seen.add(key)
                    yield item
                page_token = payload.get("nextPageToken")
                if not page_token:
                    break

    def _iter_objects_gcloud(self) -> Iterable[dict]:
        command = [
            "gcloud",
            "storage",
            "ls",
            "--recursive",
            "--json",
            f"gs://{self.config.bucket}/**",
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        payload = json.loads(result.stdout)
        for item in payload:
            if item.get("type") != "cloud_object":
                continue
            metadata = item.get("metadata") or {}
            name = metadata.get("name", "")
            if self.config.include_prefixes and not any(name.startswith(prefix) for prefix in self.config.include_prefixes):
                continue
            yield metadata


def is_candidate_archive(obj: ObjectMetadata, suffixes: tuple[str, ...], content_types: tuple[str, ...]) -> bool:
    if not obj.is_file:
        return False
    content_types_lower = {value.lower() for value in content_types}
    suffixes_lower = tuple(suffix.lower() for suffix in suffixes)
    if obj.content_type and obj.content_type.lower() in content_types_lower:
        return True
    lowered = obj.name.lower()
    return lowered.endswith(suffixes_lower)
