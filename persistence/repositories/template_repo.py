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
        scope: Optional[str] = None,
        chat_id: Optional[str] = None,
        lineage_template_id: Optional[str] = None,
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
                scope=scope or "user",
                chat_id=chat_id,
                lineage_template_id=lineage_template_id,
            )
            self.session.add(row)
        else:
            row.name = name
            row.description = description
            row.type = type_
            row.blocks_json = blocks_json
            row.steps_json = steps_json
            if scope is not None:
                row.scope = scope
            if chat_id is not None:
                row.chat_id = chat_id
            if lineage_template_id is not None:
                row.lineage_template_id = lineage_template_id
        self.session.flush()
        return row

    def search(
        self, *,
        query: str = "",
        scope: Optional[str] = None,
        owner_open_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[TemplateRow]:
        q = self.session.query(TemplateRow).filter(
            TemplateRow.archived_at.is_(None)
        )
        if query:
            like = f"%{query}%"
            q = q.filter(
                (TemplateRow.name.ilike(like)) |
                (TemplateRow.description.ilike(like))
            )
        if scope:
            q = q.filter(TemplateRow.scope == scope)
        if owner_open_id:
            q = q.filter(TemplateRow.owner_open_id == owner_open_id)
        return q.order_by(TemplateRow.updated_at.desc()) \
                 .limit(limit).offset(offset).all()

    def list_by_scope(
        self, *, scope: str,
        limit: int = 20, offset: int = 0,
    ) -> list[TemplateRow]:
        return (
            self.session.query(TemplateRow)
            .filter_by(scope=scope, archived_at=None)
            .order_by(TemplateRow.updated_at.desc())
            .limit(limit).offset(offset).all()
        )

    def list_by_lineage(self, lineage_template_id: str) -> list[TemplateRow]:
        return (
            self.session.query(TemplateRow)
            .filter_by(lineage_template_id=lineage_template_id,
                        archived_at=None)
            .all()
        )

    def get(self, template_id: str) -> Optional[TemplateRow]:
        return self.session.get(TemplateRow, template_id)

    def list_by_owner(self, owner_open_id: str) -> list[TemplateRow]:
        return (
            self.session.query(TemplateRow)
            .filter_by(owner_open_id=owner_open_id, archived_at=None)
            .all()
        )

    def list_by_chat(self, chat_id: str) -> list[TemplateRow]:
        return (
            self.session.query(TemplateRow)
            .filter_by(chat_id=chat_id, scope="chat", archived_at=None)
            .all()
        )

    def delete(self, template_id: str) -> None:
        row = self.session.get(TemplateRow, template_id)
        if row is not None:
            row.archived_at = datetime.utcnow()
            self.session.flush()