"""Phase 11: 评论事件处理服务（防循环 → 绑定过滤 → sync + notify，ADR-0033）。

事件回调运行在 SDK ws 线程：本服务必须绑定独立 DB Session 组装（runtime 负责），
严禁与主管线共享 Session。sync/notify 复用 Phase 8/9 幂等服务，
与轮询通道并发触发同一 doc 不会重复落库或重复推送。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CommentEventService:
    """处理 drive.notice.comment_add_v1 事件的业务链。"""

    def __init__(self, *, session_repo, sync_service, notify_service,
                 bot_open_id: str | None) -> None:
        self.session_repo = session_repo
        self.sync_service = sync_service
        self.notify_service = notify_service
        self.bot_open_id = bot_open_id

    def handle(self, *, file_token: str, operator_open_id: str) -> dict:
        """处理一条评论事件；异常吃掉返回 error（保长连接）。"""
        try:
            return self._handle(file_token=file_token,
                                operator_open_id=operator_open_id)
        except Exception:
            logger.exception("comment event handling failed: %s", file_token)
            return {"status": "error", "file_token": file_token}

    def _handle(self, *, file_token: str, operator_open_id: str) -> dict:
        # 1. 防循环：bot 自身评论（回执写回触发）直接忽略；
        #    拿不到 bot id 时保守跳过（宁可漏处理不冒死循环风险）
        if self.bot_open_id is None:
            return {"status": "skipped_no_bot_id"}
        if operator_open_id == self.bot_open_id:
            return {"status": "ignored_bot_self", "file_token": file_token}

        # 2. 绑定过滤：找该 doc 的活跃绑定 session（未过期）
        now = datetime.now(timezone.utc)
        hit = None
        for s in self.session_repo.list_active():
            if s.bound_doc_id != file_token:
                continue
            exp = s.bind_expires_at
            if exp is None:
                hit = s
                break
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp > now:
                hit = s
                break
        if hit is None:
            return {"status": "ignored_unbound", "file_token": file_token}

        # 3. 触发同步 + 通知（幂等，与轮询通道共用）
        self.sync_service.sync(doc_id=file_token)
        out = self.notify_service.notify_new_pending(
            doc_id=file_token, owner_open_id=hit.owner_open_id,
            chat_id=hit.source_chat_id)
        return {"status": "handled", "file_token": file_token, **out}
