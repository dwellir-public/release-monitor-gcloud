from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, Path
import re
import tarfile

_VERSION_HEADING_PATTERN = re.compile(
    r"^\s{0,3}#{1,6}\s*v?(?P<version>\d+(?:\.\d+){1,3}(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?)\s*$"
)
_NOTES_FILENAMES = (
    "release_notes.txt",
    "release-notes.txt",
    "releasenotes.txt",
    "changelog.md",
    "changes.md",
)
_MAX_NOTE_CHARS = 40_000


@dataclass(frozen=True)
class ExtractedReleaseNotes:
    text: str
    source_member: str


def extract_release_notes_for_tag_from_archive(archive_path: Path, release_tag: str) -> ExtractedReleaseNotes | None:
    if not tarfile.is_tarfile(str(archive_path)):
        return None

    try:
        with tarfile.open(archive_path, mode="r:*") as handle:
            note_members = [
                member
                for member in handle.getmembers()
                if member.isfile() and _looks_like_notes_file(member.name)
            ]
            note_members.sort(key=lambda member: _notes_member_priority(member.name))

            fallback_text: str | None = None
            fallback_source: str | None = None
            for member in note_members:
                text = _read_text_member(handle, member)
                if not text:
                    continue

                section, has_version_sections = extract_release_notes_section_for_tag(text, release_tag)
                if section:
                    return ExtractedReleaseNotes(text=section, source_member=member.name)
                if not has_version_sections and fallback_text is None:
                    fallback_text = _truncate_notes(text)
                    fallback_source = member.name

            if fallback_text and fallback_source:
                return ExtractedReleaseNotes(text=fallback_text, source_member=fallback_source)
            return None
    except (tarfile.TarError, OSError):
        return None


def extract_release_notes_section_for_tag(notes_text: str, release_tag: str) -> tuple[str | None, bool]:
    lines = notes_text.splitlines()
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _VERSION_HEADING_PATTERN.match(line)
        if not match:
            continue
        headings.append((index, match.group("version")))

    if not headings:
        text = notes_text.strip()
        if not text:
            return None, False
        return _truncate_notes(text), False

    normalized_target = _normalize_tag(release_tag)
    for index, (_, version) in enumerate(headings):
        if _normalize_tag(version) != normalized_target:
            continue
        start = headings[index][0]
        end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        section = "\n".join(lines[start:end]).strip()
        if not section:
            return None, True
        return _truncate_notes(section), True

    return None, True


def _looks_like_notes_file(member_name: str) -> bool:
    filename = PurePosixPath(member_name).name.lower()
    return filename in _NOTES_FILENAMES


def _notes_member_priority(member_name: str) -> tuple[int, int, int]:
    path = PurePosixPath(member_name)
    filename = path.name.lower()
    if filename.startswith("release"):
        base_priority = 0
    elif "change" in filename:
        base_priority = 1
    else:
        base_priority = 2
    return base_priority, len(path.parts), len(member_name)


def _read_text_member(handle: tarfile.TarFile, member: tarfile.TarInfo) -> str | None:
    extracted = handle.extractfile(member)
    if extracted is None:
        return None
    data = extracted.read()
    if not data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _normalize_tag(tag: str) -> str:
    return str(tag).strip().lower().lstrip("v")


def _truncate_notes(text: str) -> str:
    if len(text) <= _MAX_NOTE_CHARS:
        return text
    return (
        text[:_MAX_NOTE_CHARS]
        + "\n\n[release notes truncated for webhook payload size; full notes available in artifact]"
    )
