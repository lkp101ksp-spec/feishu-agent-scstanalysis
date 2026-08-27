"""Phase 6: 模板版本表 CRUD。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import TemplateVersionRow


class TemplateVersionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def insert(
        self,
        *,
        version_id: str,
        template_id: str,
        version_number: int,
        name: str,
        description: str,
        blocks_json: Optional[str] = None,
        steps_json: Optional[str] = None,
        created_by: str,
    ) -> TemplateVersionRow:
        row = TemplateVersionRow(
            version_id=version_id,
            template_id=template_id,
            version_number=version_number,
            name=name,
            description=description,
            blocks_json=blocks_json,
            steps_json=steps_json,
            created_by=created_by,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def count(self, template_id: str) -> int:
        return (
            self.session.query(TemplateVersionRow)
            .filter_by(template_id=template_id).count()
        )

    def list_by_template(self, template_id: str) -> list[TemplateVersionRow]:
        return (
            self.session.query(TemplateVersionRow)
            .filter_by(template_id=template_id)
            .order_by(TemplateVersionRow.version_number.desc())
            .all()
        )

    def get_by_version(
        self, template_id: str, version_number: int
    ) -> Optional[TemplateVersionRow]:
        return (
            self.session.query(TemplateVersionRow)
            .filter_by(template_id=template_id, version_number=version_number)
            .first()
        )

    def delete_oldest(self, template_id: str, *, keep: int = 10) -> None:
        rows = (
            self.session.query(TemplateVersionRow)
            .filter_by(template_id=template_id)
            .order_by(TemplateVersionRow.version_number.asc())
            .all()
        )
        to_delete = len(rows) - keep
        if to_delete > 0:
            for r in rows[:to_delete]:
                self.session.delete(r)
            self.session.flush()
