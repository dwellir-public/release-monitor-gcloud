from __future__ import annotations

import io
import tarfile
from pathlib import Path

from gcs_release_monitor.release_notes import (
    extract_release_notes_for_tag_from_archive,
    extract_release_notes_section_for_tag,
)


def _write_tar(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, mode="w:gz") as handle:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            handle.addfile(info, io.BytesIO(content))


def test_extract_release_notes_section_for_matching_version() -> None:
    notes = """
# v2.0.15

Important hardfork details.

# v2.0.14

Old release details.
""".strip()

    section, has_sections = extract_release_notes_section_for_tag(notes, "v2.0.15")

    assert has_sections is True
    assert section is not None
    assert "# v2.0.15" in section
    assert "Important hardfork" in section
    assert "# v2.0.14" not in section


def test_extract_release_notes_section_returns_none_when_tag_not_found() -> None:
    notes = """
# v2.0.15

Current release details.

# v2.0.14

Old release details.
""".strip()

    section, has_sections = extract_release_notes_section_for_tag(notes, "v2.0.16")

    assert has_sections is True
    assert section is None


def test_extract_release_notes_for_tag_from_archive_uses_matching_section(tmp_path: Path) -> None:
    archive = tmp_path / "test-release-notes.tar.gz"
    _write_tar(
        archive,
        {
            "megaeth-rpc-v2.0.15/RELEASE_NOTES.txt": (
                "# v2.0.15\n\nUse this one.\n\n# v2.0.14\n\nDo not include this.\n"
            ).encode("utf-8"),
            "megaeth-rpc-v2.0.15/README.md": b"readme",
        },
    )

    extracted = extract_release_notes_for_tag_from_archive(archive, "v2.0.15")

    assert extracted is not None
    assert extracted.source_member.endswith("RELEASE_NOTES.txt")
    assert "Use this one." in extracted.text
    assert "Do not include this." not in extracted.text


def test_extract_release_notes_for_tag_from_archive_falls_back_when_no_version_sections(tmp_path: Path) -> None:
    archive = tmp_path / "test-release-notes-fallback.tar.gz"
    _write_tar(
        archive,
        {
            "pkg/CHANGELOG.md": b"Single release note body with no explicit heading",
        },
    )

    extracted = extract_release_notes_for_tag_from_archive(archive, "v9.9.9")

    assert extracted is not None
    assert "Single release note body" in extracted.text
