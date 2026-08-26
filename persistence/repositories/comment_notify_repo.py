"""Phase 9: 评论动作推送日志 CRUD（去重判重，ADR-0025）。"""
from __future__ import annotations

from persistence.models import CommentNotifyRow


class CommentNotifyRepo:
    def __init__(self, session) -> None:
        self.session = session

    def has(self, comment_id: str) -> bool:
        """该评论是否已通知过。"""
        return self.session.get(CommentNotifyRow, comment_id) is not None

    def insert_many(
        self, *, doc_id: str, comment_ids: list[str],
    ) -> int:
        """批量打通知标；跳过已存在，返回实际新增数（幂等）。"""
        added = 0
        for cid in comment_ids:
            if self.session.get(CommentNotifyRow, cid) is None:
                self.session.add(CommentNotifyRow(
                    comment_id=cid, doc_id=doc_id,
                ))
                added += 1
        self.session.flush()
        return added

    def list_by_doc(self, doc_id: str) -> list[str]:
        """列出某 doc 已通知的评论 id。"""
        rows = (
            self.session.query(CommentNotifyRow)
            .filter_by(doc_id=doc_id)
            .all()
        )
        return [r.comment_id for r in rows]
