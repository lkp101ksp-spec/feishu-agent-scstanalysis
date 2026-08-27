"""Doc 文档操作：块树读取、纯文本追加、Block 列表渲染。

出站通道二选一（构造时注入）：
- sdk_client：lark-oapi SDK tenant 身份直连 docx API（生产/联调）
- cli / base_url+api_token：lark-cli 子进程 / httpx 直连（历史路径，供单测 mock）
"""
import json
from typing import Any

import httpx
import lark_oapi as lark

from feishu_adapter.client import LarkCLI, LarkCLIError

# 官方 docx block_type 数值枚举（仅 SDK 路径使用）
_SDK_HEADING_TYPE = {f"heading{i}": 2 + i for i in range(1, 10)}
_SDK_CODE_LANG = {
    "plaintext": 1, "bash": 7, "c": 10, "cpp": 16, "csharp": 17,
    "css": 18, "go": 28, "html": 31, "java": 33, "javascript": 34,
    "json": 35, "markdown": 43, "php": 49, "powershell": 51,
    "python": 54, "r": 55, "rust": 58, "shell": 63, "sql": 64,
    "typescript": 67, "yaml": 72,
}


class DocAdapter:
    """对飞书 Doc 文档的薄封装。"""

    def __init__(self, cli: LarkCLI | None = None,
                 base_url: str = "", api_token: str = "",
                 rate_limiter=None, sdk_client=None):
        self.cli = cli or LarkCLI()
        self.base_url = base_url
        self.api_token = api_token
        self.sdk_client = sdk_client
        if rate_limiter is None:
            from orchestrator.tools.bio.rate_limiter import RateLimiter
            self._rate_limiter = RateLimiter(rate=3.0, per_sec=1.0)
        else:
            self._rate_limiter = rate_limiter

    def get_block_tree(self, doc_id: str) -> list[dict[str, Any]]:
        """读取文档块树，返回有序块列表。"""
        if self.sdk_client is not None:
            return self._sdk_list_blocks(doc_id)
        result = self.cli.run(["docx", "block", "list", "--doc-id", doc_id])
        return result.get("blocks", [])

    def append_plain_text(self, doc_id: str, text: str) -> str:
        """在文档末尾追加一段纯文本块。返回新 block_id。"""
        if self.sdk_client is not None:
            blocks = [{"block_type": 2, "text": {
                "elements": [{"text_run": {"content": text}}]}}]
            children = self._sdk_create_children(doc_id, blocks)
            return children[0].get("block_id", "") if children else ""
        result = self.cli.run([
            "docx", "block", "create",
            "--doc-id", doc_id,
            "--block-type", "text",
            "--content", text,
        ])
        return result.get("block_id", "")

    def resolve_wiki_token(self, wiki_token: str) -> str:
        """wiki 节点 token → 真实 docx document_id（SDK 路径，get_node 接口）。

        需要应用具备 wiki:wiki:readonly 权限且机器人可访问该知识库节点。
        解析失败 / 节点非 docx 文档时抛 LarkCLIError。
        """
        if self.sdk_client is None:
            raise LarkCLIError("wiki 链接解析需要 SDK 直连通道（sdk_client 未注入）")
        req = (lark.BaseRequest.builder()
               .http_method(lark.HttpMethod.GET)
               .uri("/open-apis/wiki/v2/spaces/get_node")
               .token_types({lark.AccessTokenType.TENANT})
               .queries({"token": [wiki_token], "obj_type": ["wiki"]})
               .build())
        resp = self.sdk_client.request(req)
        payload = json.loads(resp.content)
        if payload.get("code") != 0:
            raise LarkCLIError(
                f"wiki get_node failed: code={payload.get('code')} "
                f"msg={payload.get('msg')}")
        node = payload.get("data", {}).get("node", {})
        if node.get("obj_type") != "docx":
            raise LarkCLIError(
                f"wiki 节点不是云文档（obj_type={node.get('obj_type')}），"
                "请绑定 docx 类型文档")
        return node.get("obj_token", "")

    # === lark-oapi SDK 直连路径 ===
    def _sdk_request(self, method, uri: str, body: dict | None = None) -> dict:
        """原始 BaseRequest 调 docx API（tenant token 由 SDK 托管），返回 data 段。"""
        builder = (lark.BaseRequest.builder()
                   .http_method(method)
                   .uri(uri)
                   .token_types({lark.AccessTokenType.TENANT})
                   .queries({"document_revision_id": ["-1"]}))
        if body is not None:
            builder = builder.body(body)
        resp = self.sdk_client.request(builder.build())
        payload = json.loads(resp.content)
        if payload.get("code") != 0:
            raise LarkCLIError(
                f"docx api failed: code={payload.get('code')} "
                f"msg={payload.get('msg')} uri={uri}")
        return payload.get("data", {})

    def _sdk_create_children(self, doc_id: str, blocks: list[dict]) -> list[dict]:
        """在文档根块末尾追加子块，返回新建块列表。"""
        data = self._sdk_request(
            lark.HttpMethod.POST,
            f"/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
            body={"children": blocks, "index": -1},
        )
        return data.get("children", [])

    def _sdk_list_blocks(self, doc_id: str) -> list[dict[str, Any]]:
        """分页拉取文档全部块（page_size=500 上限）。"""
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            uri = f"/open-apis/docx/v1/documents/{doc_id}/blocks?page_size=500"
            if page_token:
                uri += f"&page_token={page_token}"
            builder = (lark.BaseRequest.builder()
                       .http_method(lark.HttpMethod.GET)
                       .uri(uri)
                       .token_types({lark.AccessTokenType.TENANT}))
            resp = self.sdk_client.request(builder.build())
            payload = json.loads(resp.content)
            if payload.get("code") != 0:
                raise LarkCLIError(
                    f"docx list blocks failed: code={payload.get('code')} "
                    f"msg={payload.get('msg')}")
            data = payload.get("data", {})
            items.extend(data.get("items", []))
            if not data.get("has_more"):
                return items
            page_token = data.get("page_token", "")

    def _to_sdk_blocks(self, block) -> list[dict]:
        """Block → 官方数值 block_type 的子块列表（SDK 路径）。

        复杂容器/媒体块（table/image/callout 等创建受限）降级为文本块，
        保证联调链路不因单个块类型整体失败。
        """
        t = block.type
        if t == "heading":
            key = f"heading{block.level}"
            return [{"block_type": _SDK_HEADING_TYPE[key], key: {
                "elements": [{"text_run": {"content": block.text}}]}}]
        if t == "text":
            return [{"block_type": 2, "text": {
                "elements": [{"text_run": {"content": block.text}}]}}]
        if t == "code":
            lang = _SDK_CODE_LANG.get(str(block.language).lower(), 1)
            return [{"block_type": 14, "code": {
                "style": {"language": lang},
                "elements": [{"text_run": {"content": block.text}}]}}]
        if t == "mermaid":
            return [{"block_type": 14, "code": {
                "style": {"language": 1},
                "elements": [{"text_run": {
                    "content": f"[mermaid]\n{block.code}"}}]}}]
        if t == "quote":
            return [{"block_type": 15, "quote": {
                "elements": [{"text_run": {"content": block.text}}]}}]
        if t == "list":
            key = "ordered" if block.ordered else "bullet"
            return [{"block_type": 13 if block.ordered else 12, key: {
                "elements": [{"text_run": {"content": item}}]}}
                for item in block.items]
        if t == "divider":
            return [{"block_type": 22, "divider": {}}]
        # 降级：其余类型以纯文本形式落盘
        fallback = getattr(block, "text", "") or getattr(block, "url", "") \
            or getattr(block, "latex", "") or str(t)
        return [{"block_type": 2, "text": {
            "elements": [{"text_run": {"content": f"[{t}] {fallback}"}}]}}]

    def _sdk_render_blocks(self, doc_id: str, blocks) -> None:
        """SDK 路径：把 list[Block] 逐块展开后批量追加到文档末尾。"""
        batch: list[dict] = []
        for block in blocks:
            self._rate_limiter.wait()
            batch.extend(self._to_sdk_blocks(block))
            if len(batch) >= 50:  # children 单批上限 50
                self._sdk_create_children(doc_id, batch)
                batch = []
        if batch:
            self._sdk_create_children(doc_id, batch)

    # === Phase 5 ===
    def render_blocks(self, doc_id: str, blocks) -> None:
        """Phase 5: 把 list[Block] 渲染到飞书 doc。"""
        if self.sdk_client is not None:
            self._sdk_render_blocks(doc_id, blocks)
            return
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
        # === Phase 6: 8 类增量 ===
        if t == "embed":
            return {"block_type": "embed",
                    "embed": {"url": block.url, "title": block.title,
                              "description": block.description}}
        if t == "divider":
            return {"block_type": "divider", "divider": {}}
        if t == "callout":
            return {"block_type": "callout",
                    "callout": {"emoji": block.emoji, "text": block.text,
                                "color": block.color}}
        if t == "equation":
            return {"block_type": "equation",
                    "equation": {"latex": block.latex}}
        if t == "math":
            return {"block_type": "equation",
                    "equation": {"latex": block.latex,
                                 "display_mode": block.display_mode}}
        if t == "mermaid":
            return {"block_type": "code",
                    "code": {"elements": [
                        {"text_run": {"content": f"[mermaid]\n{block.code}"}}
                    ], "language": "mermaid"}}
        if t == "video":
            return {"block_type": "video",
                    "video": {"url": block.url,
                              "poster_url": block.poster_url,
                              "duration": block.duration}}
        if t == "file":
            return {"block_type": "file",
                    "file": {"file_token": block.file_token,
                             "name": block.name, "size": block.size}}
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
