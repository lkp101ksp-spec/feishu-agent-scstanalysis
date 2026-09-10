"""Drive Adapter：上传产物到飞书 Drive，分片 / 幂等 / 大小校验。

Phase 2 通过 lark-cli 子进程调用；Phase 5 切 OpenAPI。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.errors import ArtifactNotFoundError, FileTooLargeError


@dataclass
class FileMeta:
    artifact_id: str
    task_id: str
    sha256: str
    mime: str
    size: int  # bytes
    local_path: str


@dataclass
class UploadResult:
    file_token: str
    drive_url: str


class DriveAdapter:
    def __init__(
        self,
        *,
        parent_node_token: str,
        lark_cli: Any,
        artifact_repo: Any,
        max_size_mb: int = 500,
    ) -> None:
        self.parent_node_token = parent_node_token
        self.lark_cli = lark_cli
        self.artifact_repo = artifact_repo
        self.max_size_mb = max_size_mb

    def upload(self, meta: FileMeta) -> UploadResult:
        max_bytes = self.max_size_mb * 1024 * 1024
        if meta.size > max_bytes:
            raise FileTooLargeError(
                f"file {meta.artifact_id} size {meta.size}B exceeds {self.max_size_mb}MB"
            )
        try:
            existing = self.artifact_repo.get(meta.artifact_id)
        except ArtifactNotFoundError:
            raise ArtifactNotFoundError(f"artifact {meta.artifact_id} not found")
        if (
            getattr(existing, "file_token", None)
            and getattr(existing, "storage_type", None) == "drive"
        ):
            return UploadResult(
                file_token=existing.file_token,
                drive_url=getattr(existing, "drive_url", "") or "",
            )
        resp = self.lark_cli.drive_upload(
            meta.local_path,
            parent_node_token=self.parent_node_token,
            mime=meta.mime,
        )
        return UploadResult(file_token=resp["file_token"], drive_url=resp["drive_url"])

    def upload_inline(self, meta: FileMeta) -> UploadResult:
        raise NotImplementedError("upload_inline in Phase 2.1")
