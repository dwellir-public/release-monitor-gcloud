from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from gcs_release_monitor.artifact_selection import UploadCandidate
from gcs_release_monitor.monitor import MonitorService
from gcs_release_monitor.types import ObjectMetadata


class _NoUploadNextcloud:
    def upload_file(self, *_args, **_kwargs):
        raise AssertionError("nextcloud upload must not be called in webhook_only mode")

    def create_public_share(self, *_args, **_kwargs):
        raise AssertionError("nextcloud share must not be called in webhook_only mode")


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


def test_process_object_webhook_only_skips_upload_but_sends_webhook(tmp_path: Path) -> None:
    obj = _obj()
    service = object.__new__(MonitorService)
    service.config = SimpleNamespace(
        delivery_mode="webhook_only",
        temp_dir=tmp_path / "tmp",
        nextcloud=None,
        chain=SimpleNamespace(
            organization="megaeth",
            repository="megaeth-rpc",
            common_name="MegaETH RPC",
            extra_info="",
            client_name=None,
            chain_ids=(),
            genesis_hashes=(),
        ),
        release_defaults=SimpleNamespace(urgent=False, priority=3, due_date="P2D"),
    )

    service.gcs = SimpleNamespace(
        download_object=lambda _name, local_path: local_path.write_bytes(b"not-a-tar")
    )
    service.nextcloud = _NoUploadNextcloud()

    sent: dict = {}
    service.webhook = SimpleNamespace(send_release=lambda payload: sent.setdefault("payload", payload))
    service._choose_upload_candidates = lambda _archive, _temp, _obj: [
        UploadCandidate(
            local_path=tmp_path / "tmp" / "unused",
            output_name="rpc-node-v2.0.9",
            artifact_type="binary",
            source_member="pkg/rpc-node-v2.0.9",
        )
    ]
    service.config.temp_dir.mkdir(parents=True, exist_ok=True)

    record = service._process_object(obj, dry_run=False)

    assert record.nextcloud_url.startswith("gs://bucket/")
    assert "#member=" in record.nextcloud_url
    assert "payload" in sent
    assert sent["payload"]["source"]["delivery_mode"] == "webhook_only"
    assert sent["payload"]["release"]["delivery_mode"] == "webhook_only"
    assert "without Nextcloud upload" in sent["payload"]["result"]["summary"]
