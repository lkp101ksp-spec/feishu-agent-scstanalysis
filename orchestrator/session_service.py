"""Session 服务层。

按 (open_id, chat_id) 复用最近 active session，没有就新建。
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from persistence.models import SessionRow
from persistence.repositories.session_repo import SessionRepo
from shared.ulid_ import new_ulid


class SessionService:
    """对 SessionRepo 的封装，提供 get_or_create / bind_doc / bound_doc_id / is_bind_valid。"""

    def __init__(self, repo: SessionRepo):
        self.repo = repo

    def get_or_create(self, owner_open_id: str, source_chat_id: str) -> str:
        """查找 (open_id, chat_id) 下最近 active session，没有就新建。

        返回 session_id。
        """
        existing = (
            self.repo.session.query(SessionRow)
            .filter_by(owner_open_id=owner_open_id, source_chat_id=source_chat_id, status="active")
            .order_by(SessionRow.updated_at.desc())
            .first()
        )
        if existing is not None:
            return existing.session_id

        sid = new_ulid()
        self.repo.upsert(
            session_id=sid,
            owner_open_id=owner_open_id,
            source_chat_id=source_chat_id,
            bound_doc_id=None,
            bind_expires_at=None,
        )
        return sid

    def bind_doc(self, session_id: str, doc_id: str, ttl_sec: int) -> datetime:
        """把 doc_id 绑定到 session 上，TTL 后过期。返回 expires_at。"""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_sec)
        # 查询已有 owner / source 保留
        existing = self.repo.get(session_id)
        owner_open_id = existing.owner_open_id if existing else ""
        source_chat_id = existing.source_chat_id if existing else ""
        self.repo.upsert(
            session_id=session_id,
            owner_open_id=owner_open_id,
            source_chat_id=source_chat_id,
            bound_doc_id=doc_id,
            bind_expires_at=expires_at,
        )
        return expires_at

    def is_bind_valid(self, session_id: str, doc_id: str) -> bool:
        """检查 session 是否对该 doc_id 有有效绑定。"""
        row = self.repo.get(session_id)
        if row is None or row.bound_doc_id != doc_id or row.bind_expires_at is None:
            return False
        expires_at = row.bind_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)

    def bound_doc_id(self, session_id: str) -> Optional[str]:
        """返回当前有效绑定的 doc_id；过期或未绑定返回 None。"""
        row = self.repo.get(session_id)
        if row is None or row.bound_doc_id is None or row.bind_expires_at is None:
            return None
        # SQLite 写入会丢时区，统一按 naive UTC 比较
        expires_at = row.bind_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            return None
        return row.bound_doc_id