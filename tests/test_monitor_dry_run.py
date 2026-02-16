from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from gcs_release_monitor.monitor import MonitorService
from gcs_release_monitor.types import ObjectMetadata, ProcessingRecord, Snapshot


class _FakeStore:
    def __init__(self):
        self.bootstrap_calls = 0
        self.save_state_calls = 0
        self.save_snapshot_calls = 0

    def bootstrap(self):
        self.bootstrap_calls += 1

    def load_state(self):
        return SimpleNamespace(processed={})

    def load_latest_snapshot(self):
        return None

    def save_state(self, _state):
        self.save_state_calls += 1

    def save_snapshot(self, _snapshot):
        self.save_snapshot_calls += 1


class _FakeGCS:
    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot

    def list_snapshot(self):
        return self.snapshot

    def close(self):
        pass


def _obj() -> ObjectMetadata:
    return ObjectMetadata(
        bucket="bucket",
        name="v2.0.9/megaeth-rpc-v2.0.9.tar.gz",
        size=123,
        content_type="application/x-tar",
        generation="111",
        metageneration="1",
        md5_hash="x",
        crc32c="y",
        etag="z",
        updated="2026-02-16T00:00:00+00:00",
        time_created="2026-02-16T00:00:00+00:00",
    )


def test_run_once_dry_run_does_not_persist_state_or_snapshot(tmp_path: Path):
    obj = _obj()
    snapshot = Snapshot(bucket="bucket", captured_at="t1", objects={obj.object_id: obj})

    service = object.__new__(MonitorService)
    service.config = SimpleNamespace(temp_dir=tmp_path / "tmp", gcs=SimpleNamespace(bucket="bucket"))
    service.store = _FakeStore()
    service.gcs = _FakeGCS(snapshot)
    service.nextcloud = SimpleNamespace(close=lambda: None)
    service.webhook = SimpleNamespace()
    service._new_candidate_objects = lambda _previous, current: [current.objects[obj.object_id]]

    called = {"value": False}

    def fake_process(_obj, dry_run=False):
        called["value"] = True
        assert dry_run is True
        return ProcessingRecord(
            processed_at="now",
            nextcloud_path="dry-run://path",
            nextcloud_url="dry-run://url",
            share_url=None,
            webhook_delivered_at="now",
            uploads=[],
        )

    service._process_object = fake_process

    service.run_once(dry_run=True)

    assert called["value"] is True
    assert service.store.bootstrap_calls == 0
    assert service.store.save_state_calls == 0
    assert service.store.save_snapshot_calls == 0
