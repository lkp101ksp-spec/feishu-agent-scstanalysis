"""approvals 表的 CRUD。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import ApprovalRow
from shared.ulid_ import new_ulid


class ApprovalRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        task_id: str,
        tool_name: str,
        args_preview: dict,
        actor_open_id: str,
        session_id: str,
        nonce: str,
        expires_at: str,
    ) -> str:
        aid = new_ulid()
        row = ApprovalRow(
            approval_id=aid,
            task_id=task_id,
            tool_name=tool_name,
            args_preview=args_preview,
            actor_open_id=actor_open_id,
            session_id=session_id,
            nonce=nonce,
            status="pending",
            expires_at=datetime.fromisoformat(expires_at),
            created_at=datetime.now(UTC),
        )
        self.session.add(row)
        self.session.commit()
        return aid

    def get(self, approval_id: str) -> ApprovalRow:
        return (
            self.session.query(ApprovalRow).filter_by(approval_id=approval_id).one()
        )

    def get_by_nonce(self, nonce: str) -> Optional[ApprovalRow]:
        return (
            self.session.query(ApprovalRow).filter_by(nonce=nonce).one_or_none()
        )

    def resolve(self, approval_id: str, *, status: str, resolved_by: str) -> None:
        row = self.get(approval_id)
        row.status = status
        row.resolved_by = resolved_by
        row.resolved_at = datetime.now(UTC)
        self.session.commit()

    def expire_pending(self) -> int:
        now = datetime.now(UTC)
        rows = (
            self.session.query(ApprovalRow)
            .filter(ApprovalRow.status == "pending", ApprovalRow.expires_at < now)
            .all()
        )
        for r in rows:
            r.status = "expired"
            r.resolved_at = now
        self.session.commit()
        return len(rows)
