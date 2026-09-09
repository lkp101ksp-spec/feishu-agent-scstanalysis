"""Session 仓储：upsert 创建或更新会话；get 按主键查询。"""
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from persistence.models import SessionRow

# 区分"未传参"与"显式传 None"：bind 三字段未传时保持原值，显式传 None 才清除
_UNSET: Any = object()


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
        owner_open_id: Optional[str] = None,
        source_chat_id: Optional[str] = None,
        bound_doc_id: Any = _UNSET,
        bind_expires_at: Any = _UNSET,
        bind_anchor: Any = _UNSET,
        archived_at: Optional[datetime] = None,
        status: Optional[str] = None,
        approval_scope: Optional[dict[str, Any]] = None,
        origin_session_id: Optional[str] = None,
    ) -> SessionRow:
        """创建或更新会话。返回 ORM 行实例（未 commit，由调用方决定 commit 时机）。

        创建路径要求 owner_open_id/source_chat_id 非 None（列 NOT NULL）；
        更新路径只覆盖显式传入的字段：bind 三字段用 _UNSET 哨兵区分"未传"
        与"显式传 None"，status/archived_at/approval_scope/origin_session_id
        仅在非 None 时写入（freeze_session 的归档与新开会话均走此入口）。
        """
        row = self.session.get(SessionRow, session_id)
        if row is None:
            if owner_open_id is None or source_chat_id is None:
                raise ValueError(
                    f"create session requires owner_open_id/source_chat_id: {session_id!r}")
            row = SessionRow(
                session_id=session_id,
                owner_open_id=owner_open_id,
                source_chat_id=source_chat_id,
                bound_doc_id=None if bound_doc_id is _UNSET else bound_doc_id,
                bind_anchor=None if bind_anchor is _UNSET else bind_anchor,
                bind_expires_at=None if bind_expires_at is _UNSET else bind_expires_at,
            )
            if approval_scope is not None:
                row.approval_scope = approval_scope
            if origin_session_id is not None:
                row.origin_session_id = origin_session_id
            self.session.add(row)
        else:
            # 已存在时只覆盖显式传入的可变字段；owner/source 不变
            if bound_doc_id is not _UNSET:
                row.bound_doc_id = bound_doc_id
            if bind_anchor is not _UNSET:
                row.bind_anchor = bind_anchor
            if bind_expires_at is not _UNSET:
                row.bind_expires_at = bind_expires_at
            if archived_at is not None:
                row.archived_at = archived_at
            if status is not None:
                row.status = status
            if approval_scope is not None:
                row.approval_scope = approval_scope
            if origin_session_id is not None:
                row.origin_session_id = origin_session_id
        self.session.flush()
        return row

    def get(self, session_id: str) -> Optional[SessionRow]:
        """按主键查询，不存在返回 None。"""
        return self.session.get(SessionRow, session_id)

    def update_bind_expires(
            self, session_id: str,
            bind_expires_at: Optional[datetime]) -> Optional[SessionRow]:
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
