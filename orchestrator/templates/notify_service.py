"""Phase 9 T4: pending 动作推送服务（sync 后即时通知 + 日志去重，ADR-0025）。"""
from __future__ import annotations

from orchestrator.templates.comment_action_service import parse_action


class CommentNotifyService:
    """检测新增待处理动作评论并 IM 通知 owner；notify 日志表持久去重。"""

    def __init__(self, comment_repo, notify_repo, im_adapter,
                 action_parser=parse_action) -> None:
        self.comment_repo = comment_repo
        self.notify_repo = notify_repo
        self.im = im_adapter
        self.action_parser = action_parser

    def notify_new_pending(
        self, *, doc_id: str, owner_open_id: str, chat_id: str,
    ) -> dict:
        """推送未通知过的动作评论摘要；空集不发消息（防噪音）。"""
        hits = [
            c for c in self.comment_repo.list_pending(doc_id)
            if self.action_parser(c.text) is not None
            and not self.notify_repo.has(c.comment_id)
        ]
        if not hits:
            return {"notified": 0}

        lines = [f"[评论动作] doc {doc_id} 有 {len(hits)} 条待处理："]
        for c in hits:
            text = c.text if len(c.text) <= 50 else c.text[:50] + "…"
            user = c.user_name or "匿名"
            lines.append(f"- {user}: {text}")
        lines.append(f"发送 /comment-apply {doc_id} 应用")
        self.im.reply(chat_id, "\n".join(lines))

        self.notify_repo.insert_many(
            doc_id=doc_id, comment_ids=[c.comment_id for c in hits],
        )
        return {"notified": len(hits)}
