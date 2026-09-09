"""Phase 8 T4: 评论按需同步（拉取 + 展平 + 幂等落库，ADR-0019）。"""
from __future__ import annotations

from typing import Any, Optional

from feishu_adapter.comment_client import CommentClient
from persistence.models import CommentRow
from persistence.repositories.comment_repo import CommentRepo


class CommentSyncService:
    def __init__(self, client: CommentClient, comment_repo: CommentRepo) -> None:
        self.client = client
        self.comment_repo = comment_repo

    def sync(self, *, doc_id: str) -> dict[str, Any]:
        """拉取飞书评论并展平落库（root + reply 各一行）。

        返回 {"fetched": 拉取总数, "new": 新增数, "updated": 更新数,
        "deleted": 对账删除数}。Phase 18：拉取后与本地做删除对账——
        远端已消失的评论本地物理删除（编辑由 upsert 覆盖 text）。
        """
        items = self.client.list_comments(doc_id=doc_id)
        fetched = 0
        new = 0
        updated = 0
        remote_ids: set[str] = set()
        for c in items:
            root_id = c.get("comment_id", "")
            remote_ids.add(root_id)
            _, is_new = self.comment_repo.upsert_one(
                comment_id=root_id,
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
                reply_id = reply.get("reply_id") or reply.get("comment_id", "")
                remote_ids.add(reply_id)
                _, r_is_new = self.comment_repo.upsert_one(
                    comment_id=reply_id,
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
        deleted = self.comment_repo.delete_missing(doc_id, remote_ids)
        return {"fetched": fetched, "new": new, "updated": updated,
                "deleted": deleted}

    def list_stored(
        self, *, doc_id: str, block_id: Optional[str] = None,
    ) -> list[CommentRow]:
        """读取本地快照（stored API 数据源）。"""
        return self.comment_repo.list_by_doc(doc_id, block_id=block_id)
