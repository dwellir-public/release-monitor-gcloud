from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import ProcessingRecord, Snapshot


@dataclass
class MonitorState:
    processed: dict[str, ProcessingRecord]

    @staticmethod
    def empty() -> "MonitorState":
        return MonitorState(processed={})

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed": {object_id: record.as_dict() for object_id, record in self.processed.items()},
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "MonitorState":
        processed_raw = raw.get("processed") or {}
        processed: dict[str, ProcessingRecord] = {}
        for object_id, record in processed_raw.items():
            uploads = record.get("uploads") or []
            if not uploads:
                uploads = [
                    {
                        "artifact_type": "archive",
                        "artifact_name": None,
                        "source_member": None,
                        "nextcloud_path": str(record.get("nextcloud_path", "")),
                        "nextcloud_url": str(record.get("nextcloud_url", "")),
                        "share_url": str(record["share_url"]) if record.get("share_url") else None,
                    }
                ]
            processed[object_id] = ProcessingRecord(
                processed_at=str(record["processed_at"]),
                nextcloud_path=str(record["nextcloud_path"]),
                nextcloud_url=str(record["nextcloud_url"]),
                share_url=str(record["share_url"]) if record.get("share_url") else None,
                webhook_delivered_at=str(record["webhook_delivered_at"]),
                uploads=uploads,
            )
        return MonitorState(processed=processed)


class StateStore:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_file = state_dir / "state.json"
        self.latest_snapshot_file = state_dir / "snapshot-latest.json"
        self.previous_snapshot_file = state_dir / "snapshot-previous.json"

    def bootstrap(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> MonitorState:
        if not self.state_file.exists():
            return MonitorState.empty()
        with self.state_file.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return MonitorState.from_dict(raw)

    def save_state(self, state: MonitorState) -> None:
        self._write_json_atomic(self.state_file, state.as_dict())

    def load_latest_snapshot(self) -> Snapshot | None:
        if not self.latest_snapshot_file.exists():
            return None
        with self.latest_snapshot_file.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return Snapshot.from_dict(raw)

    def save_snapshot(self, snapshot: Snapshot) -> None:
        if self.latest_snapshot_file.exists():
            self.latest_snapshot_file.replace(self.previous_snapshot_file)
        self._write_json_atomic(self.latest_snapshot_file, snapshot.as_dict())

    @staticmethod
    def _write_json_atomic(target: Path, payload: dict[str, Any]) -> None:
        tmp = target.with_suffix(target.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        tmp.replace(target)
