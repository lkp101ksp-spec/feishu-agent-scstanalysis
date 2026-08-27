"""Phase 7: 飞书 doc comment API 客户端（仅 GET，不持久化）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from orchestrator.tools.bio.rate_limiter import RateLimiter


class CommentClient:
    def __init__(self, *, base_url: str, api_token: str,
                 rate_limiter: "RateLimiter") -> None:
        self.base_url = base_url
        self.api_token = api_token
        self.rate_limiter = rate_limiter

    def list_comments(self, *, doc_id: str) -> list[dict]:
        url = (
            f"{self.base_url}/open-apis/docx/v1/"
            f"documents/{doc_id}/comments"
        )
        headers = {"Authorization": f"Bearer {self.api_token}"}
        self.rate_limiter.wait()
        resp = httpx.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("items", [])

    def list_block_comments(
        self, *, doc_id: str, block_id: str,
    ) -> list[dict]:
        return [
            c for c in self.list_comments(doc_id=doc_id)
            if c.get("block_id") == block_id
        ]
