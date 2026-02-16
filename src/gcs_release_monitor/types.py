from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


ARCHIVE_SUFFIX_DEFAULTS = (".tar.gz", ".tgz", ".tar.xz", ".tar.zst", ".zip", ".gz")
CONTENT_TYPE_DEFAULTS = (
    "application/gzip",
    "application/x-gzip",
    "application/x-tar",
    "application/gzip-compressed",
    "application/octet-stream",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ObjectMetadata:
    bucket: str
    name: str
    size: int
    content_type: str | None
    generation: str
    metageneration: str | None
    md5_hash: str | None
    crc32c: str | None
    etag: str | None
    updated: str
    time_created: str | None

    @property
    def object_id(self) -> str:
        return f"{self.name}#{self.generation}"

    @property
    def gs_url(self) -> str:
        return f"gs://{self.bucket}/{self.name}"

    @property
    def is_file(self) -> bool:
        return self.size > 0 and not self.name.endswith("/")

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "name": self.name,
            "size": self.size,
            "content_type": self.content_type,
            "generation": self.generation,
            "metageneration": self.metageneration,
            "md5_hash": self.md5_hash,
            "crc32c": self.crc32c,
            "etag": self.etag,
            "updated": self.updated,
            "time_created": self.time_created,
            "object_id": self.object_id,
            "gs_url": self.gs_url,
        }


@dataclass
class Snapshot:
    bucket: str
    captured_at: str
    objects: dict[str, ObjectMetadata] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "captured_at": self.captured_at,
            "objects": {object_id: obj.as_dict() for object_id, obj in self.objects.items()},
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Snapshot":
        objects: dict[str, ObjectMetadata] = {}
        for object_id, obj in (raw.get("objects") or {}).items():
            metadata = ObjectMetadata(
                bucket=str(obj["bucket"]),
                name=str(obj["name"]),
                size=int(obj["size"]),
                content_type=obj.get("content_type"),
                generation=str(obj["generation"]),
                metageneration=obj.get("metageneration"),
                md5_hash=obj.get("md5_hash"),
                crc32c=obj.get("crc32c"),
                etag=obj.get("etag"),
                updated=str(obj["updated"]),
                time_created=obj.get("time_created"),
            )
            objects[object_id] = metadata
        return Snapshot(
            bucket=str(raw.get("bucket", "")),
            captured_at=str(raw.get("captured_at", "")),
            objects=objects,
        )


@dataclass
class ProcessingRecord:
    processed_at: str
    nextcloud_path: str
    nextcloud_url: str
    share_url: str | None
    webhook_delivered_at: str
    uploads: list[dict[str, str | None]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed_at": self.processed_at,
            "nextcloud_path": self.nextcloud_path,
            "nextcloud_url": self.nextcloud_url,
            "share_url": self.share_url,
            "webhook_delivered_at": self.webhook_delivered_at,
            "uploads": self.uploads,
        }
