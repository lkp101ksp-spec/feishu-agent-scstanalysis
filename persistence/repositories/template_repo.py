"""templates 表的 CRUD。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import TemplateRow


class TemplateRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(
        self,
        *,
        template_id: str,
        owner_open_id: str,
        name: str,
        type_: str,
        blocks_json: Optional[str] = None,
        steps_json: Optional[str] = None,
        description: str = "",
    ) -> TemplateRow:
        row = self.session.get(TemplateRow, template_id)
        if row is None:
            row = TemplateRow(
                template_id=template_id,
                owner_open_id=owner_open_id,
                name=name,
                description=description,
                type=type_,
                blocks_json=blocks_json,
                steps_json=steps_json,
            )
            self.session.add(row)
        else:
            row.name = name
            row.description = description
            row.type = type_
            row.blocks_json = blocks_json
            row.steps_json = steps_json
        self.session.flush()
        return row

    def get(self, template_id: str) -> Optional[TemplateRow]:
        return self.session.get(TemplateRow, template_id)

    def list_by_owner(self, owner_open_id: str) -> list[TemplateRow]:
        return (
            self.session.query(TemplateRow)
            .filter_by(owner_open_id=owner_open_id, archived_at=None)
            .all()
        )

    def delete(self, template_id: str) -> None:
        row = self.session.get(TemplateRow, template_id)
        if row is not None:
            row.archived_at = datetime.utcnow()
            self.session.flush()