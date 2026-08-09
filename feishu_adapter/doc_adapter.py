"""Doc 文档操作：Phase 1 仅支持块树读取与纯文本追加。

Phase 5：render_blocks() 把 list[Block] 批量渲染到飞书 doc。
"""
from typing import Any

import httpx

from feishu_adapter.client import LarkCLI


class DocAdapter:
    """对飞书 Doc 文档的薄封装。"""

    def __init__(self, cli: LarkCLI | None = None,
                 base_url: str = "", api_token: str = "",
                 rate_limiter=None):
        self.cli = cli or LarkCLI()
        self.base_url = base_url
        self.api_token = api_token
        if rate_limiter is None:
            from orchestrator.tools.bio.rate_limiter import RateLimiter
            self._rate_limiter = RateLimiter(rate=3.0, per_sec=1.0)
        else:
            self._rate_limiter = rate_limiter

    def get_block_tree(self, doc_id: str) -> list[dict[str, Any]]:
        """读取文档块树，返回有序块列表。"""
        result = self.cli.run(["docx", "block", "list", "--doc-id", doc_id])
        return result.get("blocks", [])

    def append_plain_text(self, doc_id: str, text: str) -> str:
        """在文档末尾追加一段纯文本块。返回新 block_id。"""
        result = self.cli.run([
            "docx", "block", "create",
            "--doc-id", doc_id,
            "--block-type", "text",
            "--content", text,
        ])
        return result.get("block_id", "")

    # === Phase 5 ===
    def render_blocks(self, doc_id: str, blocks) -> None:
        """Phase 5: 把 list[Block] 渲染到飞书 doc（6 类）。"""
        for block in blocks:
            self._rate_limiter.wait()
            payload = self._to_feishu_payload(block)
            self._post_block(doc_id, payload)

    def _to_feishu_payload(self, block) -> dict:
        """Phase 5: 6 类 Block → 飞书 doc API payload。"""
        t = block.type
        if t == "heading":
            level = block.level
            return {
                "block_type": f"heading{level}",
                f"heading{level}": {"elements": [
                    {"text_run": {"content": block.text}}
                ]},
            }
        if t == "text":
            return {
                "block_type": "text",
                "text": {"elements": [{"text_run": {"content": block.text}}]},
            }
        if t == "code":
            return {
                "block_type": "code",
                "code": {"elements": [{"text_run": {"content": block.text}}],
                         "language": block.language},
            }
        if t == "quote":
            return {
                "block_type": "quote_container",
                "quote_container": [{"block_type": "text",
                                     "text": {"elements": [
                                         {"text_run": {"content": block.text}}
                                     ]}}],
            }
        if t == "table":
            return {
                "block_type": "table",
                "table": {
                    "property": {"row_size": len(block.rows) + 1,
                                 "column_size": len(block.headers)},
                    "cells": [block.headers] + block.rows,
                },
            }
        if t == "list":
            list_type = "ordered_list" if block.ordered else "bullet_list"
            return {
                "block_type": list_type,
                list_type: {"elements": [
                    {"text_run": {"content": item}}
                    for item in block.items
                ]},
            }
        if t == "image":
            return {
                "block_type": "image",
                "image": {"url": block.url, "alt": block.alt},
            }
        raise ValueError(f"unsupported block type: {t}")

    def _post_block(self, doc_id: str, payload: dict) -> None:
        """Phase 5: HTTP POST 飞书 doc API。生产中用真实 API；测试用 respx mock。"""
        if not self.base_url:
            return  # 测试 / dry-run
        url = f"{self.base_url}/docx/v1/blocks"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        resp = httpx.post(
            url, headers=headers,
            json={"doc_id": doc_id, "block": payload},
            timeout=10,
        )
        resp.raise_for_status()