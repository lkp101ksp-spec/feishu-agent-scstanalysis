"""Phase 7: 协作评论聚合 + IM 文本渲染。"""
from __future__ import annotations

from typing import Optional

from feishu_adapter.comment_client import CommentClient


class CommentService:
    def __init__(self, client: CommentClient) -> None:
        self.client = client

    def fetch_thread(
        self, *, doc_id: str, block_id: Optional[str] = None,
    ) -> str:
        if block_id:
            comments = self.client.list_block_comments(
                doc_id=doc_id, block_id=block_id)
        else:
            comments = self.client.list_comments(doc_id=doc_id)
        if not comments:
            return "（无评论）"
        lines = ["评论列表："]
        for c in comments:
            user = c.get("user_name") or "匿名"
            text = c.get("text", "")
            lines.append(f"- {user}: {text}")
            for reply in c.get("replies", []) or []:
                r_user = reply.get("user_name") or "匿名"
                r_text = reply.get("text", "")
                lines.append(f"  ↳ {r_user}: {r_text}")
        return "\n".join(lines)
