"""Phase 8: 模板收藏 CRUD（先查后写幂等，ADR-0022）。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from persistence.models import TemplateFavoriteRow


class TemplateFavoriteRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self, *, favorite_id: str, template_id: str, user_open_id: str,
    ) -> None:
        """收藏；已收藏静默（幂等）。"""
        existing = (
            self.session.query(TemplateFavoriteRow)
            .filter_by(template_id=template_id, user_open_id=user_open_id)
            .one_or_none()
        )
        if existing is not None:
            return
        self.session.add(TemplateFavoriteRow(
            favorite_id=favorite_id,
            template_id=template_id,
            user_open_id=user_open_id,
        ))
        self.session.flush()

    def remove(self, *, template_id: str, user_open_id: str) -> None:
        """取消收藏；不存在静默（幂等）。"""
        (
            self.session.query(TemplateFavoriteRow)
            .filter_by(template_id=template_id, user_open_id=user_open_id)
            .delete()
        )
        self.session.flush()

    def list_by_user(self, user_open_id: str) -> list[str]:
        """我的收藏（收藏时间倒序）。"""
        rows = (
            self.session.query(TemplateFavoriteRow)
            .filter_by(user_open_id=user_open_id)
            .order_by(TemplateFavoriteRow.created_at.desc())
            .all()
        )
        return [r.template_id for r in rows]
