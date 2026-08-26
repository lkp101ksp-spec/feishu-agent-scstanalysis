"""Phase 8 T4: 评论按需同步（拉取 + 展平 + 幂等落库，ADR-0019）。"""
from __future__ import annotations

from typing import Optional


class CommentSyncService:
    def __init__(self, client, comment_repo) -> None:
        self.client = client
        self.comment_repo = comment_repo

    def sync(self, *, doc_id: str) -> dict:
        """拉取飞书评论并展平落库（root + reply 各一行）。

        返回 {"fetched": 拉取总数, "new": 新增数, "updated": 更新数}。
        """
        items = self.client.list_comments(doc_id=doc_id)
        fetched = 0
        new = 0
        updated = 0
        for c in items:
            _, is_new = self.comment_repo.upsert_one(
                comment_id=c.get("comment_id", ""),
                doc_id=doc_id,
                block_id=c.get("block_id"),
                user_id=c.get("user_id", ""),
                user_name=c.get("user_name", ""),
                text=c.get("text", ""),
                is_reply=False,
                parent_comment_id=None,
                resolved=bool(c.get("resolved", False)),
            )
            fetched += 1
            if is_new:
                new += 1
            else:
                updated += 1
            for reply in c.get("replies", []) or []:
                _, r_is_new = self.comment_repo.upsert_one(
                    comment_id=reply.get("reply_id") or reply.get("comment_id", ""),
                    doc_id=doc_id,
                    block_id=c.get("block_id"),
                    user_id=reply.get("user_id", ""),
                    user_name=reply.get("user_name", ""),
                    text=reply.get("text", ""),
                    is_reply=True,
                    parent_comment_id=c.get("comment_id", ""),
                    resolved=bool(reply.get("resolved", False)),
                )
                fetched += 1
                if r_is_new:
                    new += 1
                else:
                    updated += 1
        return {"fetched": fetched, "new": new, "updated": updated}

    def list_stored(
        self, *, doc_id: str, block_id: Optional[str] = None,
    ) -> list:
        """读取本地快照（stored API 数据源）。"""
        return self.comment_repo.list_by_doc(doc_id, block_id=block_id)
