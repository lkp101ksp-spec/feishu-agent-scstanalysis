"""Phase 8: 模板标签 CRUD（先查后写幂等，ADR-0022）。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from persistence.models import TemplateTagRow


class TemplateTagRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self, *, tag_id: str, template_id: str, tag: str, created_by: str,
    ) -> TemplateTagRow:
        """打标签；已存在 (template_id, tag) 时静默返回既有行（幂等）。"""
        existing = (
            self.session.query(TemplateTagRow)
            .filter_by(template_id=template_id, tag=tag)
            .one_or_none()
        )
        if existing is not None:
            return existing
        row = TemplateTagRow(
            tag_id=tag_id,
            template_id=template_id,
            tag=tag,
            created_by=created_by,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def remove(self, *, template_id: str, tag: str) -> None:
        """摘标签；不存在静默（幂等）。"""
        (
            self.session.query(TemplateTagRow)
            .filter_by(template_id=template_id, tag=tag)
            .delete()
        )
        self.session.flush()

    def list_by_template(self, template_id: str) -> list[str]:
        rows = (
            self.session.query(TemplateTagRow)
            .filter_by(template_id=template_id)
            .order_by(TemplateTagRow.created_at.asc())
            .all()
        )
        return [r.tag for r in rows]

    def find_template_ids_by_tag(self, tag: str) -> list[str]:
        rows = (
            self.session.query(TemplateTagRow)
            .filter_by(tag=tag)
            .order_by(TemplateTagRow.created_at.desc())
            .all()
        )
        return [r.template_id for r in rows]

    def list_all(self) -> list[tuple[str, str]]:
        """Phase 19：返回全部 (template_id, tag) 行（标签推荐数据源）。

        标签表规模为模板数量级（小表），全量扫描可接受。
        """
        rows = self.session.query(TemplateTagRow).all()
        return [(r.template_id, r.tag) for r in rows]
