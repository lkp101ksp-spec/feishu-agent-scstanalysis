"""Session 服务层。

Phase 1 简化模型：每个 (open_id, chat_id) 复用最新 session；
没有就新建。生产环境可改为按 thread_ts 或 chat_id 严格 1:1。
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from persistence.repositories.session_repo import SessionRepo
from shared.ulid_ import new_ulid


class SessionService:
    """对 SessionRepo 的封装，提供 get_or_create / bind_doc / bound_doc_id / is_bind_valid。"""

    def __init__(self, repo: SessionRepo):
        self.repo = repo

    def get_or_create(self, owner_open_id: str, source_chat_id: str) -> str:
        """创建新 session，返回 session_id。

        Phase 1 总是新建（不查询复用）；后续可改为按 (open_id, chat_id) 查找最近 active session 复用。
        """
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
        return row.bind_expires_at > datetime.now(timezone.utc)

    def bound_doc_id(self, session_id: str) -> Optional[str]:
        """返回当前有效绑定的 doc_id；过期或未绑定返回 None。"""
        row = self.repo.get(session_id)
        if row is None or row.bound_doc_id is None or row.bind_expires_at is None:
            return None
        if row.bind_expires_at <= datetime.now(timezone.utc):
            return None
        return row.bound_doc_id