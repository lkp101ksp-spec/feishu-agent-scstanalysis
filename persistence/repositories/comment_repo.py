"""Phase 8: 评论本地快照 CRUD（幂等 upsert / pending 扫描 / 打标）。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import CommentRow, _utcnow


class CommentRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_one(
        self,
        *,
        comment_id: str,
        doc_id: str,
        block_id: Optional[str] = None,
        user_id: str = "",
        user_name: str = "",
        text: str = "",
        is_reply: bool = False,
        parent_comment_id: Optional[str] = None,
        resolved: bool = False,
    ) -> tuple[CommentRow, bool]:
        """幂等 upsert 一条评论；返回 (row, is_new)。

        已存在时仅更新 text / resolved / synced_at；
        processed_at 不被覆盖（防动作重放，ADR-0019）。
        """
        row = self.session.get(CommentRow, comment_id)
        if row is None:
            row = CommentRow(
                comment_id=comment_id,
                doc_id=doc_id,
                block_id=block_id,
                user_id=user_id,
                user_name=user_name,
                text=text,
                is_reply=is_reply,
                parent_comment_id=parent_comment_id,
                resolved=resolved,
            )
            self.session.add(row)
            self.session.flush()
            return row, True
        row.text = text
        row.resolved = resolved
        row.synced_at = _utcnow()
        self.session.flush()
        return row, False

    def list_by_doc(
        self, doc_id: str, block_id: Optional[str] = None,
    ) -> list[CommentRow]:
        q = self.session.query(CommentRow).filter_by(doc_id=doc_id)
        if block_id is not None:
            q = q.filter_by(block_id=block_id)
        return q.order_by(CommentRow.created_at.asc()).all()

    def list_pending(self, doc_id: str) -> list[CommentRow]:
        """未处理评论（processed_at IS NULL），动作 apply 的数据源。"""
        return (
            self.session.query(CommentRow)
            .filter_by(doc_id=doc_id)
            .filter(CommentRow.processed_at.is_(None))
            .order_by(CommentRow.created_at.asc())
            .all()
        )

    def mark_processed(self, comment_id: str) -> None:
        """动作执行成功后打标；重复调用幂等。"""
        row = self.session.get(CommentRow, comment_id)
        if row is not None and row.processed_at is None:
            row.processed_at = _utcnow()
            self.session.flush()
