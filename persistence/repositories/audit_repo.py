"""Audit 仓储：write 不可变审计，list_recent 按时间倒序。"""
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import AuditLogRow


class AuditRepo:
    """对 audit_logs 表的薄封装。Phase 1 仅支持追加写入。"""

    def __init__(self, session: Session):
        self.session = session

    def write(
        self,
        audit_id: str,
        actor_type: str,
        actor_id: str,
        action: str,
        target_type: str,
        target_id: str,
        detail: Optional[dict] = None,
    ) -> AuditLogRow:
        """追加一条审计日志。"""
        row = AuditLogRow(
            audit_id=audit_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail_json=detail or {},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_recent(self, limit: int = 50) -> list[AuditLogRow]:
        """按 created_at 倒序返回最近 limit 条。"""
        return (
            self.session.query(AuditLogRow)
            .order_by(AuditLogRow.created_at.desc())
            .limit(limit)
            .all()
        )
