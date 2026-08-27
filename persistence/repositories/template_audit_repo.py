"""Phase 7: 模板审核日志 CRUD。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import TemplateAuditRow


class TemplateAuditRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def insert(
        self, *,
        audit_id: str,
        template_id: str,
        action: str,
        actor_open_id: str,
        reason: Optional[str] = None,
    ) -> TemplateAuditRow:
        row = TemplateAuditRow(
            audit_id=audit_id,
            template_id=template_id,
            action=action,
            actor_open_id=actor_open_id,
            reason=reason,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_by_template(self, template_id: str) -> list[TemplateAuditRow]:
        return (
            self.session.query(TemplateAuditRow)
            .filter_by(template_id=template_id)
            .order_by(TemplateAuditRow.created_at.desc())
            .all()
        )
