from __future__ import annotations

import logging
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from .artifact_selection import ArtifactSelectionError, UploadCandidate, select_upload_candidates
from .config import AppConfig, DELIVERY_MODE_WEBHOOK_ONLY
from .gcs_client import GCSClient, is_candidate_archive
from .nextcloud_client import NextcloudClient
from .release_notes import ExtractedReleaseNotes, extract_release_notes_for_tag_from_archive
from .state import StateStore
from .types import ObjectMetadata, ProcessingRecord, Snapshot, now_iso
from .webhook_client import WebhookClient

logger = logging.getLogger(__name__)


_VERSION_PATTERN = re.compile(r"v\d+(?:\.\d+){1,3}(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?")


class MonitorService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.gcs = GCSClient(config.gcs)
        self.nextcloud = NextcloudClient(config.nextcloud) if config.nextcloud else None
        self.webhook = WebhookClient(config.webhook)
        self.store = StateStore(config.state_dir)

    def run_forever(self, dry_run: bool = False) -> None:
        if not dry_run:
            self.store.bootstrap()
        logger.info(
            "Starting monitor loop with interval=%ss dry_run=%s",
            self.config.poll_interval_seconds,
            dry_run,
        )
        while True:
            try:
                self.run_once(dry_run=dry_run)
            except Exception:
                logger.exception("Polling cycle failed")
            time.sleep(self.config.poll_interval_seconds)

    def run_once(self, dry_run: bool = False) -> None:
        if not dry_run:
            self.store.bootstrap()
        self.config.temp_dir.mkdir(parents=True, exist_ok=True)
        state = self.store.load_state()
        previous_snapshot = self.store.load_latest_snapshot()
        current_snapshot = self.gcs.list_snapshot()

        candidates = self._new_candidate_objects(previous_snapshot, current_snapshot)
        if not candidates:
            logger.info(
                "No new candidate artifacts in bucket=%s at %s",
                self.config.gcs.bucket,
                current_snapshot.captured_at,
            )
            if not dry_run:
                self.store.save_snapshot(current_snapshot)
            return

        logger.info("Detected %s new candidate artifacts (dry_run=%s)", len(candidates), dry_run)
        for obj in candidates:
            if obj.object_id in state.processed:
                logger.info("Skipping already processed object_id=%s", obj.object_id)
                continue
            record = self._process_object(obj, dry_run=dry_run)
            if not dry_run:
                state.processed[obj.object_id] = record
                self.store.save_state(state)

        if not dry_run:
            self.store.save_snapshot(current_snapshot)
        else:
            logger.info("Dry run complete: no state or snapshot files updated")

    def close(self) -> None:
        if self.nextcloud:
            self.nextcloud.close()
        self.gcs.close()

    def _new_candidate_objects(self, previous: Snapshot | None, current: Snapshot) -> list[ObjectMetadata]:
        previous_ids = set(previous.objects.keys()) if previous else set()
        new_ids = [object_id for object_id in current.objects.keys() if object_id not in previous_ids]

        candidates = [
            current.objects[object_id]
            for object_id in new_ids
            if is_candidate_archive(
                current.objects[object_id],
                self.config.gcs.include_suffixes,
                self.config.gcs.include_content_types,
            )
        ]
        return sorted(candidates, key=lambda item: item.updated)

    def _process_object(self, obj: ObjectMetadata, dry_run: bool = False) -> ProcessingRecord:
        logger.info("Processing new object %s", obj.gs_url)
        webhook_only = self.config.delivery_mode == DELIVERY_MODE_WEBHOOK_ONLY

        release_tag = extract_release_tag(obj.name, obj.generation)
        extracted_notes: ExtractedReleaseNotes | None = None

        with tempfile.TemporaryDirectory(prefix="gcs-monitor-", dir=str(self.config.temp_dir)) as temp_dir:
            filename = Path(obj.name).name
            local_path = Path(temp_dir) / filename
            self.gcs.download_object(obj.name, local_path)

            extracted_notes = extract_release_notes_for_tag_from_archive(local_path, release_tag)
            if extracted_notes:
                logger.info(
                    "Extracted release notes for %s from member=%s",
                    release_tag,
                    extracted_notes.source_member,
                )

            candidates = self._choose_upload_candidates(local_path, Path(temp_dir), obj)

            uploaded_items: list[dict[str, str | None]] = []
            for candidate in candidates:
                if dry_run:
                    if webhook_only:
                        remote_path = self._webhook_only_path(obj, candidate)
                        nextcloud_url = self._webhook_only_link(obj, candidate)
                        logger.info(
                            "Dry run: webhook_only mode would skip upload for artifact_type=%s source_member=%s",
                            candidate.artifact_type,
                            candidate.source_member or candidate.output_name,
                        )
                    else:
                        remote_path = self._build_remote_path(candidate.output_name, obj)
                        nextcloud_url = f"dry-run://nextcloud/{remote_path}"
                        logger.info(
                            "Dry run: would upload artifact_type=%s source_member=%s to %s",
                            candidate.artifact_type,
                            candidate.source_member or candidate.output_name,
                            remote_path,
                        )
                    share_url: str | None = None
                    download_url: str | None = None
                    if self.config.nextcloud and self.config.nextcloud.create_public_share:
                        logger.info("Dry run: would create Nextcloud public share for %s", remote_path)
                elif webhook_only:
                    remote_path = self._webhook_only_path(obj, candidate)
                    nextcloud_url = self._webhook_only_link(obj, candidate)
                    share_url = None
                    download_url = None
                    logger.info(
                        "Webhook-only mode: skipping Nextcloud upload for artifact_type=%s source_member=%s",
                        candidate.artifact_type,
                        candidate.source_member or candidate.output_name,
                    )
                else:
                    remote_path = self._build_remote_path(candidate.output_name, obj)
                    if not self.nextcloud:
                        raise RuntimeError("nextcloud client is not configured")
                    nextcloud_url = self.nextcloud.upload_file(candidate.local_path, remote_path)
                    share_url = None
                    if self.config.nextcloud and self.config.nextcloud.create_public_share:
                        share_url = self.nextcloud.create_public_share(remote_path)
                    download_url = self._public_download_url(share_url, candidate.output_name)
                uploaded_items.append(
                    {
                        "artifact_type": candidate.artifact_type,
                        "artifact_name": candidate.output_name,
                        "source_member": candidate.source_member,
                        "nextcloud_path": remote_path,
                        "nextcloud_url": nextcloud_url,
                        "share_url": share_url,
                        "download_url": download_url,
                    }
                )

        release_payload = self._build_release_payload(
            obj,
            uploaded_items,
            release_tag=release_tag,
            extracted_notes=extracted_notes,
        )
        if dry_run:
            logger.info(
                "Dry run: would send one webhook for %s with %s uploaded artifacts (primary=%s)",
                release_payload["release_meta"]["tag_name"],
                len(uploaded_items),
                release_payload["release_meta"]["html_url"],
            )
        else:
            self.webhook.send_release(release_payload)

        now = now_iso()
        if dry_run:
            logger.info("Dry run processed object_id=%s (no upload/webhook performed)", obj.object_id)
        elif webhook_only:
            logger.info(
                "Processed object_id=%s in webhook_only mode and delivered webhook",
                obj.object_id,
            )
        else:
            logger.info("Processed object_id=%s and delivered webhook", obj.object_id)
        primary = uploaded_items[0]
        return ProcessingRecord(
            processed_at=now,
            nextcloud_path=str(primary["nextcloud_path"]),
            nextcloud_url=str(primary["nextcloud_url"]),
            share_url=str(primary["share_url"]) if primary.get("share_url") else None,
            webhook_delivered_at=now,
            uploads=uploaded_items,
        )

    def _choose_upload_candidates(
        self,
        local_archive_path: Path,
        temp_dir: Path,
        obj: ObjectMetadata,
    ) -> list[UploadCandidate]:
        try:
            selected = select_upload_candidates(
                local_archive_path,
                temp_dir / "selected",
                self.config.chain,
                self.config.artifact_selection,
            )
            if selected:
                logger.info(
                    "Selected extracted artifacts for object_id=%s: %s",
                    obj.object_id,
                    [candidate.output_name for candidate in selected],
                )
                return selected
        except ArtifactSelectionError as exc:
            logger.warning(
                "Artifact selection failed for object_id=%s: %s",
                obj.object_id,
                exc,
            )

        if not self.config.artifact_selection.fallback_to_archive:
            raise RuntimeError(
                f"artifact selection failed for {obj.object_id} and fallback_to_archive is disabled"
            )
        logger.info("Falling back to archive upload for object_id=%s", obj.object_id)
        return [
            UploadCandidate(
                local_path=local_archive_path,
                output_name=Path(obj.name).name,
                artifact_type="archive",
                source_member=None,
            )
        ]

    def _build_remote_path(self, filename: str, obj: ObjectMetadata) -> str:
        if not self.config.nextcloud:
            raise RuntimeError("nextcloud configuration is not available in webhook_only mode")
        remote_root = self.config.nextcloud.remote_dir
        organization = self.config.chain.organization
        release_tag = extract_release_tag(obj.name, obj.generation)
        version_prefix = f"{release_tag}-"
        versioned_filename = filename if filename.startswith(version_prefix) else f"{version_prefix}{filename}"
        filename_with_generation = f"{versioned_filename}-g{obj.generation}"
        return "/".join(
            [
                remote_root,
                organization,
                filename_with_generation,
            ]
        )

    @staticmethod
    def _webhook_only_path(obj: ObjectMetadata, candidate: UploadCandidate) -> str:
        if candidate.source_member:
            return f"{obj.name}::{candidate.source_member}"
        return obj.name

    @staticmethod
    def _webhook_only_link(obj: ObjectMetadata, candidate: UploadCandidate) -> str:
        if candidate.source_member:
            return f"{obj.gs_url}#member={quote(candidate.source_member, safe='')}"
        return obj.gs_url

    @staticmethod
    def _artifact_link(item: dict[str, str | None]) -> str:
        return str(item.get("download_url") or item.get("share_url") or item["nextcloud_url"])

    @staticmethod
    def _public_download_url(share_url: str | None, artifact_name: str | None) -> str | None:
        if not share_url or not artifact_name:
            return None
        share_base = share_url.split("?", maxsplit=1)[0].rstrip("/")
        return f"{share_base}/download/{quote(artifact_name, safe='')}"

    def _build_release_payload(
        self,
        obj: ObjectMetadata,
        uploaded_items: list[dict[str, str | None]],
        release_tag: str | None = None,
        extracted_notes: ExtractedReleaseNotes | None = None,
    ) -> dict:
        if not uploaded_items:
            raise RuntimeError(f"uploaded_items is empty for {obj.object_id}")
        webhook_only = self.config.delivery_mode == DELIVERY_MODE_WEBHOOK_ONLY

        primary_link = self._artifact_link(uploaded_items[0])
        tag = release_tag or extract_release_tag(obj.name, obj.generation)
        chain = {
            "organization": self.config.chain.organization,
            "repository": self.config.chain.repository,
            "common_name": self.config.chain.common_name,
            "extra_info": self.config.chain.extra_info,
            "source": "webhook",
        }
        if self.config.chain.client_name:
            chain["client_name"] = self.config.chain.client_name
        if self.config.chain.chain_ids:
            chain["chain_ids"] = list(self.config.chain.chain_ids)
        if self.config.chain.genesis_hashes:
            chain["genesis_hashes"] = list(self.config.chain.genesis_hashes)

        link_lines = [
            f"- {item['artifact_type']}: {self._artifact_link(item)}"
            for item in uploaded_items
        ]
        links_block = "\n".join(link_lines)
        if webhook_only:
            summary_prefix = f"New release artifacts detected in gs://{obj.bucket}/{obj.name}. "
            mode_summary = (
                f"Selected {len(uploaded_items)} artifact(s) for webhook-only delivery "
                "without Nextcloud upload. "
            )
            key_change_prefix = "Selected"
        else:
            summary_prefix = f"New release artifacts mirrored from gs://{obj.bucket}/{obj.name}. "
            mode_summary = f"Uploaded {len(uploaded_items)} artifact(s). "
            key_change_prefix = "Mirrored"
        summary = (
            f"{summary_prefix}"
            f"{mode_summary}Size={obj.size} bytes, updated={obj.updated}.\n\n"
            f"Artifact links:\n{links_block}"
        )
        if extracted_notes:
            summary += f"\n\nRelease notes extracted from archive member `{extracted_notes.source_member}`."

        key_changes = [f"Artifact source: {obj.gs_url}"] + [
            f"{key_change_prefix} {item['artifact_type']}: {self._artifact_link(item)}"
            for item in uploaded_items
        ]
        if extracted_notes:
            key_changes.append(f"Release notes source: {extracted_notes.source_member}")

        payload = {
            "event_type": "gcs_release_detected",
            "event_version": "1",
            "source": {
                "type": "gcs-poller",
                "bucket": obj.bucket,
                "object_id": obj.object_id,
                "detected_at": now_iso(),
                "delivery_mode": self.config.delivery_mode,
            },
            "chain": chain,
            "release_meta": {
                "html_url": primary_link,
                "tag_name": tag,
            },
            "release": {
                "source": "gcs",
                "bucket": obj.bucket,
                "name": obj.name,
                "generation": obj.generation,
                "metageneration": obj.metageneration,
                "size": obj.size,
                "content_type": obj.content_type,
                "md5_hash": obj.md5_hash,
                "crc32c": obj.crc32c,
                "etag": obj.etag,
                "updated": obj.updated,
                "time_created": obj.time_created,
                "gs_url": obj.gs_url,
                "delivery_mode": self.config.delivery_mode,
                "nextcloud_path": uploaded_items[0]["nextcloud_path"],
                "nextcloud_url": uploaded_items[0]["nextcloud_url"],
                "share_url": uploaded_items[0]["share_url"],
                "download_url": uploaded_items[0]["download_url"],
                "artifact_type": uploaded_items[0]["artifact_type"],
                "artifact_name": uploaded_items[0]["artifact_name"],
                "source_member": uploaded_items[0]["source_member"],
                "uploads": uploaded_items,
            },
            "result": {
                "urgent": self.config.release_defaults.urgent,
                "priority": self.config.release_defaults.priority,
                "due_date": self.config.release_defaults.due_date,
                "explicit_deadline": None,
                "summary": summary,
                "key_changes": key_changes,
                "reasoning": "Artifact-based release signal from bucket metadata.",
            },
        }
        if extracted_notes:
            payload["release_note"] = extracted_notes.text
            payload["release_notes"] = extracted_notes.text
            payload["release"]["release_notes"] = extracted_notes.text
            payload["release"]["release_notes_source"] = extracted_notes.source_member
        return payload


def extract_release_tag(object_name: str, fallback_generation: str) -> str:
    filename = Path(object_name).name
    match = _VERSION_PATTERN.search(filename)
    if match:
        return match.group(0)
    folder_parts = Path(object_name).parts
    for part in reversed(folder_parts[:-1]):
        match = _VERSION_PATTERN.search(part)
        if match:
            return match.group(0)
    return f"gcs-{fallback_generation}"


def diff_snapshot(previous: Snapshot | None, current: Snapshot) -> tuple[set[str], set[str]]:
    previous_ids = set(previous.objects.keys()) if previous else set()
    current_ids = set(current.objects.keys())
    return current_ids - previous_ids, previous_ids - current_ids
