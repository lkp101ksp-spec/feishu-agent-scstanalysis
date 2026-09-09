"""Phase 9 T5: 评论自动同步后台轮询 worker（ADR-0024）。

tick() 为纯同步单轮（可单测）；start_async/stop 仅是 asyncio 薄壳。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from orchestrator.templates.comment_sync_service import CommentSyncService
from orchestrator.templates.notify_service import CommentNotifyService
from persistence.repositories.session_repo import SessionRepo

logger = logging.getLogger(__name__)


class CommentAutoSyncWorker:
    """扫描活跃绑定 doc → sync → 推送检查（单 doc 异常隔离）。"""

    def __init__(self, *, session_repo: SessionRepo,
                 sync_service: CommentSyncService,
                 notify_service: CommentNotifyService,
                 interval_sec: int = 300,
                 session: Session | None = None) -> None:
        self.session_repo = session_repo
        self.sync_service = sync_service
        self.notify_service = notify_service
        self.interval_sec = interval_sec
        # 独立轮询 session：每轮成功后由本 worker 负责 commit（repo 只 flush）
        self.session = session
        self._task: asyncio.Task[None] | None = None

    def _now(self) -> datetime:
        """当前时间（测试可注入固定时钟）。"""
        return datetime.now(timezone.utc)

    def tick(self) -> dict[str, int]:
        """单轮：活跃绑定 doc 去重后逐个 sync + notify。"""
        now = self._now()
        # doc_id 去重（保留第一个 session 的 owner/chat）
        seen: dict[str, tuple[str, str]] = {}
        for s in self.session_repo.list_active():
            if not s.bound_doc_id:
                continue
            exp = s.bind_expires_at
            if exp is not None:
                if exp.tzinfo is None:
                    from datetime import timezone as _tz
                    exp = exp.replace(tzinfo=_tz.utc)
                if exp <= now:
                    continue
            seen.setdefault(s.bound_doc_id,
                            (s.owner_open_id, s.source_chat_id))

        synced = 0
        notified_total = 0
        for doc_id, (owner, chat) in seen.items():
            try:
                self.sync_service.sync(doc_id=doc_id)
                synced += 1
                out = self.notify_service.notify_new_pending(
                    doc_id=doc_id, owner_open_id=owner, chat_id=chat,
                )
                notified_total += out.get("notified", 0)
            except Exception:
                logger.exception("auto sync failed for doc %s", doc_id)
        # 轮询 session 收尾：成功数据提交，失败回滚（repo 只 flush）
        if self.session is not None:
            try:
                self.session.commit()
            except Exception:
                logger.exception("poll session commit failed")
                try:
                    self.session.rollback()
                except Exception:
                    logger.warning("poll session rollback failed")
        return {"synced": synced, "notified_total": notified_total}

    async def _run_loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_sec)
            self.tick()

    def start_async(self) -> None:
        """启动后台循环（无运行中的事件循环时安全跳过）。"""
        if self._task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(self._run_loop())

    def stop(self) -> None:
        """取消后台任务；幂等。"""
        if self._task is not None:
            self._task.cancel()
            self._task = None
