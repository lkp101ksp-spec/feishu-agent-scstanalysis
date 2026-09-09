"""Session 服务层。

按 (open_id, chat_id) 复用最近 active session，没有就新建。
Phase 3：freeze_session 把旧 session 标 archived，开新 session 并继承 bind（如果未过期）。
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from persistence.models import SessionRow
from persistence.repositories.audit_repo import AuditRepo
from persistence.repositories.session_freeze_repo import SessionFreezeRepo
from persistence.repositories.session_repo import SessionRepo
from shared.ulid_ import new_ulid


class SessionService:
    """对 SessionRepo 的封装，提供 get_or_create / bind_doc / bound_doc_id / is_bind_valid。

    Phase 3：freeze_session / freeze_repo / audit_repo。
    """

    def __init__(self, repo: SessionRepo,
                 freeze_repo: Optional[SessionFreezeRepo] = None,
                 audit_repo: Optional[AuditRepo] = None):
        self.repo = repo
        self.freeze_repo = freeze_repo
        self.audit_repo = audit_repo

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

    def bind_doc(self, session_id: str, doc_id: str, ttl_sec: int,
                 anchor: Optional[str] = None) -> datetime:
        """把 doc_id 绑定到 session 上，TTL 后过期。返回 expires_at。

        anchor：可选写入锚点文字（/bind-doc <链接> @锚点），
        绑定后写入会插到锚点块之后；None 表示追加到文档末尾。
        """
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
            bind_anchor=anchor,
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

    # === Phase 3 ===
    def freeze_session(self, *, session_id: str, summary: str,
                       trigger_ratio: float) -> str:
        """冻结旧 session，开新 session 并继承 bind（如果未过期）。"""
        origin = self.repo.get(session_id)
        # freeze 目标 session 必然存在（调用方从活跃 session 触发）；None 属编程错误
        assert origin is not None
        # 1. 旧 session 标 archived（upsert 部分更新：bind 字段未传则保留原值）
        self.repo.upsert(
            session_id=session_id,
            archived_at=datetime.now(timezone.utc),
            status="archived",
        )
        # 2. 决定 bind_doc 是否继承
        inherited_bind = None
        inherited_expires = None
        # DB 读回可能是 naive datetime（sqlite 丢时区），与 bound_doc_id() 同样补 UTC
        expires_at = origin.bind_expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if (origin.bound_doc_id
                and expires_at is not None
                and expires_at > datetime.now(timezone.utc)):
            inherited_bind = origin.bound_doc_id
            inherited_expires = expires_at
        # 3. 开新 session
        new_sid = new_ulid()
        self.repo.upsert(
            session_id=new_sid,
            owner_open_id=origin.owner_open_id,
            source_chat_id=origin.source_chat_id,
            bound_doc_id=inherited_bind,
            bind_expires_at=inherited_expires,
            approval_scope=origin.approval_scope or {},
            origin_session_id=session_id,
        )
        # 4. 写 session_freezes
        if self.freeze_repo is not None:
            self.freeze_repo.create(
                origin_session_id=session_id,
                new_session_id=new_sid,
                summary_id=None,
                trigger_ratio=trigger_ratio,
            )
        # 5. 审计
        if self.audit_repo is not None:
            self.audit_repo.write(
                audit_id=new_ulid(),
                actor_type="system", actor_id="session_service",
                action="freeze_session", target_type="session",
                target_id=session_id, detail={
                    "new_session_id": new_sid, "trigger_ratio": trigger_ratio,
                    "inherited_bind": bool(inherited_bind),
                },
            )
        return new_sid
