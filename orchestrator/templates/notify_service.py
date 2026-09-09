"""Phase 9 T4: pending 动作推送服务（sync 后即时通知 + 日志去重，ADR-0025）。"""
from __future__ import annotations

from typing import Any, Callable, Optional

from feishu_adapter.im_adapter import IMAdapter
from orchestrator.templates.comment_action_service import (
    ParsedAction,
    parse_action,
)
from persistence.repositories.comment_notify_repo import CommentNotifyRepo
from persistence.repositories.comment_repo import CommentRepo


class CommentNotifyService:
    """检测新增待处理动作评论并 IM 通知 owner；notify 日志表持久去重。

    notify_all=False（默认）：只推含指令的评论（防噪音，ADR-0025）；
    notify_all=True：全量新评论都推（spec §14.4 遗留项 3 的开关）。
    """

    def __init__(self, comment_repo: CommentRepo, notify_repo: CommentNotifyRepo,
                 im_adapter: IMAdapter,
                 action_parser: Callable[[str], Optional[ParsedAction]] = parse_action,
                 notify_all: bool = False) -> None:
        self.comment_repo = comment_repo
        self.notify_repo = notify_repo
        self.im = im_adapter
        self.action_parser = action_parser
        self.notify_all = notify_all

    def notify_new_pending(
        self, *, doc_id: str, owner_open_id: str, chat_id: str,
    ) -> dict[str, Any]:
        """推送未通知过的新评论摘要；空集不发消息（防噪音）。"""
        hits = []
        has_action = False
        for c in self.comment_repo.list_pending(doc_id):
            if self.notify_repo.has(c.comment_id):
                continue
            is_action = self.action_parser(c.text) is not None
            has_action = has_action or is_action
            if is_action or self.notify_all:
                hits.append(c)
        if not hits:
            return {"notified": 0}

        title = "[评论提醒]" if self.notify_all else "[评论动作]"
        lines = [f"{title} doc {doc_id} 有 {len(hits)} 条待处理："]
        for c in hits:
            text = c.text if len(c.text) <= 50 else c.text[:50] + "…"
            user = c.user_name or "匿名"
            lines.append(f"- {user}: {text}")
        # 含指令评论才提示 apply 入口；纯闲聊评论列表不附指令提示
        if has_action:
            lines.append(f"发送 /comment-apply {doc_id} 应用")
        self.im.reply(chat_id, "\n".join(lines))

        self.notify_repo.insert_many(
            doc_id=doc_id, comment_ids=[c.comment_id for c in hits],
        )
        return {"notified": len(hits)}
