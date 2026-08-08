"""/bind-doc 服务层：校验 + 写 session + 写 audit。"""
import re
from datetime import datetime

from persistence.repositories.audit_repo import AuditRepo
from shared.errors import BindDocInvalidError
from shared.ulid_ import new_ulid

from orchestrator.session_service import SessionService


_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


class BindDocService:
    """绑定飞书文档到当前会话。

    校验规则：
    - doc_id 非空
    - doc_id 匹配 `[A-Za-z0-9_-]{6,64}`
    """

    def __init__(self, session_service: SessionService, audit_repo: AuditRepo, ttl_sec: int):
        self.session_service = session_service
        self.audit_repo = audit_repo
        self.ttl_sec = ttl_sec

    def bind(self, session_id: str, owner_open_id: str, doc_id: str) -> datetime:
        """绑定 doc_id 到 session。返回过期时间。"""
        if not doc_id or not _DOC_ID_RE.match(doc_id):
            raise BindDocInvalidError(f"invalid doc_id: {doc_id!r}")

        expires_at = self.session_service.bind_doc(
            session_id=session_id, doc_id=doc_id, ttl_sec=self.ttl_sec
        )

        self.audit_repo.write(
            audit_id=new_ulid(),
            actor_type="user",
            actor_id=owner_open_id,
            action="bind_doc",
            target_type="session",
            target_id=session_id,
            detail={"doc_id": doc_id, "expires_at": expires_at.isoformat()},
        )

        return expires_at