from gcs_release_monitor.gcs_client import is_candidate_archive
from gcs_release_monitor.types import ObjectMetadata


def _obj(name: str, content_type: str | None, size: int = 1) -> ObjectMetadata:
    return ObjectMetadata(
        bucket="bucket",
        name=name,
        size=size,
        content_type=content_type,
        generation="1",
        metageneration="1",
        md5_hash=None,
        crc32c=None,
        etag=None,
        updated="2026-02-16T00:00:00+00:00",
        time_created="2026-02-16T00:00:00+00:00",
    )


def test_candidate_matches_content_type() -> None:
    obj = _obj("artifact.unknown", "application/x-tar")
    assert is_candidate_archive(obj, (".tar.gz",), ("application/x-tar",))


def test_candidate_matches_suffix() -> None:
    obj = _obj("artifact.tar.gz", "text/plain")
    assert is_candidate_archive(obj, (".tar.gz",), ())


def test_candidate_rejects_zero_size() -> None:
    obj = _obj("artifact.tar.gz", "application/x-tar", size=0)
    assert not is_candidate_archive(obj, (".tar.gz",), ("application/x-tar",))
