from gcs_release_monitor.monitor import diff_snapshot, extract_release_tag
from gcs_release_monitor.monitor import MonitorService
from gcs_release_monitor.types import ObjectMetadata, Snapshot
from types import SimpleNamespace


def _obj(name: str, generation: str) -> ObjectMetadata:
    return ObjectMetadata(
        bucket="bucket",
        name=name,
        size=100,
        content_type="application/x-tar",
        generation=generation,
        metageneration="1",
        md5_hash="abc",
        crc32c="def",
        etag="etag",
        updated="2026-02-16T00:00:00+00:00",
        time_created="2026-02-16T00:00:00+00:00",
    )


def test_extract_release_tag_prefers_filename_version() -> None:
    assert extract_release_tag("v2.0.16/megaeth-rpc-v2.0.16.tar.gz", "123") == "v2.0.16"


def test_extract_release_tag_falls_back_to_generation() -> None:
    assert extract_release_tag("releases/latest/build.tar.gz", "177123") == "gcs-177123"


def test_diff_snapshot_detects_add_and_remove() -> None:
    previous = Snapshot(bucket="bucket", captured_at="t0", objects={"a#1": _obj("a", "1")})
    current = Snapshot(bucket="bucket", captured_at="t1", objects={"b#1": _obj("b", "1")})

    added, removed = diff_snapshot(previous, current)

    assert added == {"b#1"}
    assert removed == {"a#1"}


def test_build_release_payload_includes_all_uploaded_links() -> None:
    service = object.__new__(MonitorService)
    service.config = SimpleNamespace(
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
    obj = _obj("v2.0.16/megaeth-rpc-v2.0.16.tar.gz", "123")
    uploads = [
        {
            "artifact_type": "binary",
            "artifact_name": "rpc-node-v2.0.16",
            "source_member": "x/rpc-node-v2.0.16",
            "nextcloud_path": "EXTERNAL/FILESHARES/CLIENT_BINARIES/megaeth/rpc-node-v2.0.16-g123",
            "nextcloud_url": "https://nextcloud.example/rpc-node-v2.0.16",
            "share_url": "https://nextcloud.example/s/rpc",
            "download_url": "https://nextcloud.example/s/rpc/download/rpc-node-v2.0.16",
        },
        {
            "artifact_type": "genesis",
            "artifact_name": "genesis.json",
            "source_member": "x/mainnet/genesis.json",
            "nextcloud_path": "EXTERNAL/FILESHARES/CLIENT_BINARIES/megaeth/genesis.json-g123",
            "nextcloud_url": "https://nextcloud.example/genesis.json",
            "share_url": "https://nextcloud.example/s/gen",
            "download_url": "https://nextcloud.example/s/gen/download/genesis.json",
        },
    ]

    payload = service._build_release_payload(obj, uploads)

    assert payload["release_meta"]["html_url"] == "https://nextcloud.example/s/rpc/download/rpc-node-v2.0.16"
    assert payload["release_meta"]["tag_name"] == "v2.0.16"
    assert "Artifact links:" in payload["result"]["summary"]
    assert "https://nextcloud.example/s/rpc/download/rpc-node-v2.0.16" in payload["result"]["summary"]
    assert "https://nextcloud.example/s/gen/download/genesis.json" in payload["result"]["summary"]
    assert payload["release"]["download_url"] == "https://nextcloud.example/s/rpc/download/rpc-node-v2.0.16"
    assert len(payload["release"]["uploads"]) == 2


def test_public_download_url_escapes_filename() -> None:
    assert (
        MonitorService._public_download_url("https://nextcloud.example/s/token", "rpc node")
        == "https://nextcloud.example/s/token/download/rpc%20node"
    )
