from types import SimpleNamespace

from gcs_release_monitor.monitor import MonitorService, diff_snapshot, extract_release_tag
from gcs_release_monitor.release_notes import ExtractedReleaseNotes
from gcs_release_monitor.types import ObjectMetadata, Snapshot


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


def test_build_remote_path_prefixes_filename_with_release_tag() -> None:
    service = object.__new__(MonitorService)
    service.config = SimpleNamespace(
        nextcloud=SimpleNamespace(remote_dir="EXTERNAL/FILESHARES/CLIENT_BINARIES"),
        chain=SimpleNamespace(organization="megaeth"),
    )
    obj = _obj("v2.0.16/megaeth-rpc-v2.0.16.tar.gz", "123")

    remote_path = service._build_remote_path("genesis.json", obj)

    assert remote_path == "EXTERNAL/FILESHARES/CLIENT_BINARIES/megaeth/v2.0.16-genesis.json-g123"


def test_build_remote_path_does_not_double_prefix_release_tag() -> None:
    service = object.__new__(MonitorService)
    service.config = SimpleNamespace(
        nextcloud=SimpleNamespace(remote_dir="EXTERNAL/FILESHARES/CLIENT_BINARIES"),
        chain=SimpleNamespace(organization="megaeth"),
    )
    obj = _obj("v2.0.16/megaeth-rpc-v2.0.16.tar.gz", "123")

    remote_path = service._build_remote_path("v2.0.16-genesis.json", obj)

    assert remote_path == "EXTERNAL/FILESHARES/CLIENT_BINARIES/megaeth/v2.0.16-genesis.json-g123"


def test_build_remote_path_uses_generation_tag_when_version_missing() -> None:
    service = object.__new__(MonitorService)
    service.config = SimpleNamespace(
        nextcloud=SimpleNamespace(remote_dir="EXTERNAL/FILESHARES/CLIENT_BINARIES"),
        chain=SimpleNamespace(organization="megaeth"),
    )
    obj = _obj("releases/latest/build.tar.gz", "177123")

    remote_path = service._build_remote_path("genesis.json", obj)

    assert remote_path == "EXTERNAL/FILESHARES/CLIENT_BINARIES/megaeth/gcs-177123-genesis.json-g177123"


def test_diff_snapshot_detects_add_and_remove() -> None:
    previous = Snapshot(bucket="bucket", captured_at="t0", objects={"a#1": _obj("a", "1")})
    current = Snapshot(bucket="bucket", captured_at="t1", objects={"b#1": _obj("b", "1")})

    added, removed = diff_snapshot(previous, current)

    assert added == {"b#1"}
    assert removed == {"a#1"}


def test_build_release_payload_includes_all_uploaded_links() -> None:
    service = object.__new__(MonitorService)
    service.config = SimpleNamespace(
        delivery_mode="full",
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
    assert "release_note" not in payload
    assert len(payload["release"]["uploads"]) == 2


def test_build_release_payload_includes_extracted_release_notes() -> None:
    service = object.__new__(MonitorService)
    service.config = SimpleNamespace(
        delivery_mode="full",
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
        }
    ]
    notes = ExtractedReleaseNotes(
        text="# v2.0.16\\n\\nCritical hardfork details.",
        source_member="megaeth-rpc-v2.0.16/RELEASE_NOTES.txt",
    )

    payload = service._build_release_payload(obj, uploads, extracted_notes=notes)

    assert payload["release_note"] == notes.text
    assert payload["release_notes"] == notes.text
    assert payload["release"]["release_notes"] == notes.text
    assert payload["release"]["release_notes_source"] == notes.source_member
    assert "Release notes source" in "\\n".join(payload["result"]["key_changes"])


def test_public_download_url_escapes_filename() -> None:
    assert (
        MonitorService._public_download_url("https://nextcloud.example/s/token", "rpc node")
        == "https://nextcloud.example/s/token/download/rpc%20node"
    )
