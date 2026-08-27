"""Session 仓储：upsert 创建或更新会话；get 按主键查询。"""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from persistence.models import SessionRow


class SessionRepo:
    """对 sessions 表的薄封装。

    upsert() 按 session_id 主键创建或覆盖写入；
    get() 按主键查询；不存在返回 None。
    """

    def __init__(self, session: Session):
        self.session: Session = session  # 暴露给服务层做复合查询

    def upsert(
        self,
        session_id: str,
        owner_open_id: str,
        source_chat_id: str,
        bound_doc_id: Optional[str],
        bind_expires_at: Optional[datetime],
        bind_anchor: Optional[str] = None,
    ) -> SessionRow:
        """创建或更新会话。返回 ORM 行实例（未 commit，由调用方决定 commit 时机）。"""
        row = self.session.get(SessionRow, session_id)
        if row is None:
            row = SessionRow(
                session_id=session_id,
                owner_open_id=owner_open_id,
                source_chat_id=source_chat_id,
                bound_doc_id=bound_doc_id,
                bind_anchor=bind_anchor,
                bind_expires_at=bind_expires_at,
            )
            self.session.add(row)
        else:
            # 已存在时只覆盖可变字段；owner/source 不变
            row.bound_doc_id = bound_doc_id
            row.bind_anchor = bind_anchor
            row.bind_expires_at = bind_expires_at
        self.session.flush()
        return row

    def get(self, session_id: str) -> Optional[SessionRow]:
        """按主键查询，不存在返回 None。"""
        return self.session.get(SessionRow, session_id)

    def update_bind_expires(self, session_id: str, bind_expires_at) -> Optional[SessionRow]:
        """仅更新 bind_expires_at；不改变其他字段。"""
        row = self.session.get(SessionRow, session_id)
        if row is None:
            return None
        row.bind_expires_at = bind_expires_at
        self.session.flush()
        return row

    def list_active(self) -> list[SessionRow]:
        """列出 status='active' 的 session（Phase 3 续期卡片扫描用）。"""
        return (
            self.session.query(SessionRow).filter_by(status="active").all()
        )
