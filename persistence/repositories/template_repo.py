"""templates 表的 CRUD。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import case, func, literal, select
from sqlalchemy.orm import Session

from persistence.models import (
    TemplateFavoriteRow,
    TemplateRow,
    TemplateTagRow,
)


def _is_postgres(session: Session) -> bool:
    """Phase 9: 方言检测（PG 走 tsvector，其余降级 LIKE，ADR-0026）。"""
    bind = getattr(session, "bind", None)
    return bind is not None and bind.dialect.name == "postgresql"


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

    def search_v2(
        self, *,
        query: str = "",
        tag: Optional[str] = None,
        scope: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[tuple[TemplateRow, float]]:
        """Phase 9 融合检索（ADR-0026）。

        全文（PG tsvector / SQLite LIKE 降级）+ 标签精确过滤 + 收藏数 boost：
        final = text_score + LEAST(fav_count, 5) * 0.5
        返回 (TemplateRow, final_score) 元组列表，按分值倒序。
        """
        base = self.session.query(TemplateRow, literal(0.0).label("__s"))
        base = base.filter(TemplateRow.archived_at.is_(None))
        if scope:
            base = base.filter(TemplateRow.scope == scope)
        if tag:
            base = base.filter(TemplateRow.template_id.in_(
                select(TemplateTagRow.template_id)
                .where(TemplateTagRow.tag == tag.strip().lower())
            ))

        # 全文相关度（方言分支）
        if query:
            if _is_postgres(self.session):
                # WHERE 用与 0002 迁移 GIN 表达式索引一致的表达式（可命中索引）
                ts_vec = func.to_tsvector(
                    "simple",
                    func.coalesce(TemplateRow.name, "")
                    + " " + func.coalesce(TemplateRow.description, ""),
                )
                ts_query = func.plainto_tsquery("simple", query)
                name_vec = func.to_tsvector(
                    "simple", func.coalesce(TemplateRow.name, "")
                )
                # 档位与 SQLite 分支对齐（name=2.0 / desc=1.0），
                # ts_rank 仅作同档内细分排序（ADR-0026 融合公式）
                text_score = case(
                    (name_vec.op("@@")(ts_query), 2.0 + func.ts_rank(ts_vec, ts_query)),
                    (ts_vec.op("@@")(ts_query), 1.0 + func.ts_rank(ts_vec, ts_query)),
                    else_=0.0,
                )
                base = base.filter(ts_vec.op("@@")(ts_query))
            else:
                like = f"%{query}%"
                text_score = case(
                    (TemplateRow.name.ilike(like), 2.0),
                    (TemplateRow.description.ilike(like), 1.0),
                    else_=0.0,
                )
        else:
            text_score = literal(0.0)

        # 收藏热度 boost（方言分支：PG least / SQLite 标量 min）
        fav_count = select(func.count()).select_from(TemplateFavoriteRow) \
            .where(TemplateFavoriteRow.template_id == TemplateRow.template_id) \
            .scalar_subquery()
        if _is_postgres(self.session):
            capped = func.least(fav_count, 5)
        else:
            capped = func.min(fav_count, 5)
        score_col = (text_score + capped * 0.5).label("score")

        # 沿用 base 过滤条件，select 列换成 row + score
        q = base.with_entities(TemplateRow, score_col)
        result = q.order_by(
            score_col.desc(), TemplateRow.updated_at.desc(),
        ).limit(limit).offset(offset).all()
        return [(r[0], float(r[1])) for r in result]

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
            row.archived_at = datetime.now(UTC)
            self.session.flush()
