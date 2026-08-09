"""artifacts 表 CRUD（Phase 2 扩字段 + 新方法）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import ArtifactRow
from shared.ulid_ import new_ulid


class ArtifactRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        task_id: str,
        kind: str,
        storage_type: str = "pending",
        storage_ref: Optional[str] = None,
        sha256: Optional[str] = None,
        mime: Optional[str] = None,
        size_bytes: Optional[int] = None,
        caption: Optional[str] = None,
    ) -> str:
        aid = new_ulid()
        row = ArtifactRow(
            artifact_id=aid,
            task_id=task_id,
            kind=kind,
            storage_type=storage_type,
            storage_ref=storage_ref,
            sha256=sha256,
            mime=mime,
            size_bytes=size_bytes,
            caption=caption,
            status="pending",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.session.add(row)
        self.session.commit()
        return aid

    def get(self, artifact_id: str) -> ArtifactRow:
        return (
            self.session.query(ArtifactRow).filter_by(artifact_id=artifact_id).one()
        )

    def get_by_task(self, task_id: str) -> list[ArtifactRow]:
        return self.session.query(ArtifactRow).filter_by(task_id=task_id).all()

    def update_storage(
        self,
        artifact_id: str,
        *,
        storage_type: str,
        storage_ref: Optional[str] = None,
        file_token: Optional[str] = None,
        drive_url: Optional[str] = None,
        mime: Optional[str] = None,
        size_bytes: Optional[int] = None,
        sha256: Optional[str] = None,
        caption: Optional[str] = None,
    ) -> None:
        row = self.get(artifact_id)
        row.storage_type = storage_type
        if storage_ref is not None:
            row.storage_ref = storage_ref
        if file_token is not None:
            row.file_token = file_token
        if drive_url is not None:
            row.drive_url = drive_url
        if mime is not None:
            row.mime = mime
        if size_bytes is not None:
            row.size_bytes = size_bytes
        if sha256 is not None:
            row.sha256 = sha256
        if caption is not None:
            row.caption = caption
        row.status = "ready"
        row.updated_at = datetime.utcnow()
        self.session.commit()