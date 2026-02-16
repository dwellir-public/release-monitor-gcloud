from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from gcs_release_monitor.artifact_selection import ArtifactSelectionError, select_upload_candidates
from gcs_release_monitor.config import ArtifactSelectionConfig, ArtifactSelectionRule, ChainConfig


def _write_tar(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, mode="w:gz") as handle:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))


def _chain() -> ChainConfig:
    return ChainConfig(
        organization="megaeth",
        repository="megaeth-rpc",
        common_name="MegaETH",
        extra_info="",
        client_name=None,
        chain_ids=(),
        genesis_hashes=(),
    )


def _config() -> ArtifactSelectionConfig:
    return ArtifactSelectionConfig(
        enabled=True,
        fallback_to_archive=True,
        default_binary_patterns=(),
        default_genesis_patterns=(),
        rules=(
            ArtifactSelectionRule(
                organization="megaeth",
                repository="megaeth-rpc",
                binary_patterns=("rpc-node-*", "*/rpc-node-*"),
                genesis_patterns=("mainnet/genesis.json", "*/mainnet/genesis.json"),
            ),
        ),
    )


def test_select_upload_candidates_extracts_binary_and_genesis(tmp_path: Path) -> None:
    archive_path = tmp_path / "release.tar.gz"
    _write_tar(
        archive_path,
        {
            "megaeth-rpc-v2.0.9/rpc-node-v2.0.9": b"binary-data",
            "megaeth-rpc-v2.0.9/mainnet/genesis.json": b"{}",
            "megaeth-rpc-v2.0.9/README.txt": b"notes",
        },
    )

    selected = select_upload_candidates(archive_path, tmp_path / "extract", _chain(), _config())

    assert [candidate.artifact_type for candidate in selected] == ["binary", "genesis"]
    assert selected[0].output_name == "rpc-node-v2.0.9"
    assert selected[1].output_name == "genesis.json"
    assert selected[0].local_path.exists()
    assert selected[1].local_path.exists()


def test_select_upload_candidates_raises_when_required_files_missing(tmp_path: Path) -> None:
    archive_path = tmp_path / "release.tar.gz"
    _write_tar(
        archive_path,
        {
            "megaeth-rpc-v2.0.9/rpc-node-v2.0.9": b"binary-data",
        },
    )

    with pytest.raises(ArtifactSelectionError):
        select_upload_candidates(archive_path, tmp_path / "extract", _chain(), _config())


def test_select_upload_candidates_returns_empty_when_no_rule_matches(tmp_path: Path) -> None:
    archive_path = tmp_path / "release.tar.gz"
    _write_tar(archive_path, {"x/rpc-node-v2.0.9": b"binary"})
    chain = ChainConfig(
        organization="other",
        repository="other-repo",
        common_name="Other",
        extra_info="",
        client_name=None,
        chain_ids=(),
        genesis_hashes=(),
    )

    selected = select_upload_candidates(archive_path, tmp_path / "extract", chain, _config())
    assert selected == []
