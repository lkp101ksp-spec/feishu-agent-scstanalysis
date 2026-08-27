"""ApprovalService + ApprovalPolicy + HMAC 签名工具。

- ApprovalPolicy：bind_doc 覆盖判定（仅 write_doc 同 doc 且未过期）
- ApprovalService.request_sync：同步入口；Phase 2 用 mock approve=True
- HMAC：飞书卡片回调验签

Phase 2.1：request_sync 接真实卡片发送 + asyncio.Future 等回调
Phase 5：换 Redis pub/sub 跨进程
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Any


def _hmac_sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class ApprovalPolicy:
    """仅豁免 write_doc（同一 doc，bind_doc 未过期）。"""

    def can_skip_approval(self, tool_name: str, args: dict, session: Any) -> bool:
        if tool_name != "write_doc":
            return False
        if not getattr(session, "bound_doc_id", None):
            return False
        if not getattr(session, "bind_expires_at", None):
            return False
        if session.bind_expires_at < datetime.utcnow():
            return False
        return args.get("doc_id") == session.bound_doc_id


class ApprovalService:
    def __init__(
        self,
        *,
        im_adapter=None,
        approval_repo=None,
        audit_repo=None,
        secret: str = "phase2-dev-secret-change-me",
    ) -> None:
        self.im_adapter = im_adapter
        self.approval_repo = approval_repo
        self.audit_repo = audit_repo
        self.secret = secret
        self.policy = ApprovalPolicy()

    def request_sync(
        self,
        *,
        tool_name: str,
        args_preview: dict,
        actor_open_id: str,
        session: Any,
    ) -> bool:
        """Phase 2 同步版：bind_doc 覆盖 → True；否则按 mock 行为（默认 False）。

        Phase 2.1 接入：发卡片 + 阻塞等回调 + TTL 超时 → False
        """
        if self.policy.can_skip_approval(tool_name, args_preview, session):
            return True
        # Phase 2 简化：无 im_adapter 时默认拒绝
        return False

    def verify_callback(self, body: bytes, signature: str) -> bool:
        return hmac.compare_digest(_hmac_sign(body, self.secret), signature)

    def new_nonce(self) -> str:
        return secrets.token_hex(16)
