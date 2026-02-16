from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
import tarfile

from .config import ArtifactSelectionConfig, ArtifactSelectionRule, ChainConfig


@dataclass(frozen=True)
class UploadCandidate:
    local_path: Path
    output_name: str
    artifact_type: str
    source_member: str | None


class ArtifactSelectionError(RuntimeError):
    pass


def select_upload_candidates(
    archive_path: Path,
    extraction_dir: Path,
    chain: ChainConfig,
    config: ArtifactSelectionConfig,
) -> list[UploadCandidate]:
    if not config.enabled:
        return []
    if not tarfile.is_tarfile(str(archive_path)):
        return []

    rule = _match_rule(chain, config)
    if rule is None:
        return []

    try:
        with tarfile.open(archive_path, mode="r:*") as handle:
            members = [member for member in handle.getmembers() if member.isfile()]
            binary_member = _find_member_by_patterns(members, rule.binary_patterns)
            genesis_member = _find_member_by_patterns(members, rule.genesis_patterns)
            if binary_member is None or genesis_member is None:
                raise ArtifactSelectionError("required binary/genesis members not found")

            extraction_dir.mkdir(parents=True, exist_ok=True)
            binary_candidate = _extract_member(
                handle,
                binary_member,
                extraction_dir / f"binary-{Path(binary_member.name).name}",
                artifact_type="binary",
            )
            genesis_candidate = _extract_member(
                handle,
                genesis_member,
                extraction_dir / f"genesis-{Path(genesis_member.name).name}",
                artifact_type="genesis",
            )
            return [binary_candidate, genesis_candidate]
    except (tarfile.TarError, OSError) as exc:
        raise ArtifactSelectionError(str(exc)) from exc


def _match_rule(chain: ChainConfig, config: ArtifactSelectionConfig) -> ArtifactSelectionRule | None:
    for rule in config.rules:
        if rule.organization and rule.organization != chain.organization:
            continue
        if rule.repository and rule.repository != chain.repository:
            continue
        return rule

    if config.default_binary_patterns and config.default_genesis_patterns:
        return ArtifactSelectionRule(
            organization=None,
            repository=None,
            binary_patterns=config.default_binary_patterns,
            genesis_patterns=config.default_genesis_patterns,
        )
    return None


def _find_member_by_patterns(members: list[tarfile.TarInfo], patterns: tuple[str, ...]) -> tarfile.TarInfo | None:
    for pattern in patterns:
        matches = [member for member in members if _matches(member.name, pattern)]
        if matches:
            matches.sort(key=lambda member: member.name)
            return matches[0]
    return None


def _matches(member_name: str, pattern: str) -> bool:
    basename = Path(member_name).name
    return fnmatch(member_name, pattern) or fnmatch(basename, pattern)


def _extract_member(
    handle: tarfile.TarFile,
    member: tarfile.TarInfo,
    destination: Path,
    artifact_type: str,
) -> UploadCandidate:
    if member.size <= 0:
        raise ArtifactSelectionError(f"member has invalid size: {member.name}")
    fileobj = handle.extractfile(member)
    if fileobj is None:
        raise ArtifactSelectionError(f"failed to read member: {member.name}")
    data = fileobj.read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return UploadCandidate(
        local_path=destination,
        output_name=Path(member.name).name,
        artifact_type=artifact_type,
        source_member=member.name,
    )
