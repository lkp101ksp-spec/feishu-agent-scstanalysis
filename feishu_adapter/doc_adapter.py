"""Doc 文档操作：块树读取、纯文本追加、Block 列表渲染。

出站通道二选一（构造时注入）：
- sdk_client：lark-oapi SDK tenant 身份直连 docx API（生产/联调）
- cli / base_url+api_token：lark-cli 子进程 / httpx 直连（历史路径，供单测 mock）
"""
import json
from typing import TYPE_CHECKING, Any

import httpx
import lark_oapi as lark

from feishu_adapter.client import LarkCLI, LarkCLIError

if TYPE_CHECKING:
    from orchestrator.tools.bio.rate_limiter import RateLimiter

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
                 rate_limiter: "RateLimiter | None" = None,
                 sdk_client: Any = None) -> None:
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
        blocks: list[dict[str, Any]] = result.get("blocks", [])
        return blocks

    def upload_doc_image(self, doc_id: str, image_path: str) -> str:
        """上传图片素材 → file_token（parent_node 需为 image block_id）。

        POST /open-apis/drive/v1/medias/upload（parent_type=docx_image）；
        仅 SDK 路径，需 drive:file:upload 权限。一般不单独调用——
        用 insert_doc_image 走完整三步流程。
        """
        import os

        if self.sdk_client is None:
            raise LarkCLIError("upload_doc_image requires sdk_client")
        size = os.path.getsize(image_path)
        with open(image_path, "rb") as f:
            request = (lark.drive.v1.UploadAllMediaRequest.builder()
                       .request_body(
                           lark.drive.v1.UploadAllMediaRequestBody.builder()
                           .file_name(os.path.basename(image_path))
                           .parent_type("docx_image")
                           .parent_node(doc_id)
                           .size(size)
                           .file(f)
                           .build())
                       .build())
            resp = self.sdk_client.drive.v1.media.upload_all(request)
        if not resp.success():
            raise LarkCLIError(
                f"docx image upload failed: code={resp.code} msg={resp.msg}")
        return resp.data.file_token or ""

    def insert_doc_image(self, doc_id: str, image_path: str,
                         *, index: int = -1) -> str:
        """本地图片插入文档（官方 FAQ 三步流程，Phase 20）。

        ① 建空 image block（children 接口，image 必须为空对象——
          带 token 会 1770001 invalid param，真机 2026-09-01）
        ② drive medias 上传（parent_type=docx_image，parent_node=
          ① 的 block_id——用 doc_id 会被拒）→ file_token
        ③ PATCH 该 block replace_image(token)
        返回 image block_id；任一步失败抛 LarkCLIError。
        """
        import logging as _logging

        log = _logging.getLogger(__name__)
        # ① 空 image block
        children = self._sdk_create_children(
            doc_id, [{"block_type": 27, "image": {}}], index=index)
        if not children:
            raise LarkCLIError("create empty image block returned nothing")
        block_id: str = children[0].get("block_id", "")
        if not block_id:
            raise LarkCLIError("create empty image block returned no block_id")
        # ② 上传素材（parent_node = image block_id）
        file_token = self.upload_doc_image(block_id, image_path)
        if not file_token:
            raise LarkCLIError("image upload returned empty file_token")
        # ③ replace_image
        self._sdk_request(
            lark.HttpMethod.PATCH,
            f"/open-apis/docx/v1/documents/{doc_id}/blocks/{block_id}",
            body={"replace_image": {"token": file_token}},
        )
        log.info("doc image inserted: doc=%s block=%s file=%s",
                 doc_id, block_id, image_path)
        return block_id

    def create_document(self, title: str,
                        folder_token: str | None = None) -> str:
        """新建空云文档（POST /open-apis/docx/v1/documents），返回 document_id。

        folder_token 缺省建到应用根目录；仅 SDK 直连通道。
        独立 BaseRequest（不带 docx 块接口的 document_revision_id 查询参数）。
        """
        if self.sdk_client is None:
            raise LarkCLIError("create_document 需要 SDK 直连通道（sdk_client 未注入）")
        body: dict[str, Any] = {"title": title}
        if folder_token:
            body["folder_token"] = folder_token
        req = (lark.BaseRequest.builder()
               .http_method(lark.HttpMethod.POST)
               .uri("/open-apis/docx/v1/documents")
               .token_types({lark.AccessTokenType.TENANT})
               .body(body)
               .build())
        resp = self.sdk_client.request(req)
        payload = json.loads(resp.raw.content)
        if payload.get("code") != 0:
            raise LarkCLIError(
                f"create document failed: code={payload.get('code')} "
                f"msg={payload.get('msg')}")
        doc_id: str = payload.get("data", {}).get("document", {}) \
            .get("document_id", "")
        if not doc_id:
            raise LarkCLIError("create_document returned empty document_id")
        return doc_id

    def grant_doc_view(self, doc_id: str, open_id: str) -> None:
        """授权指定用户可阅读文档（drive permissions members，perm=view）。

        仅 SDK 直连通道；应用需 drive:drive 权限域。
        """
        if self.sdk_client is None:
            raise LarkCLIError("grant_doc_view 需要 SDK 直连通道（sdk_client 未注入）")
        req = (lark.BaseRequest.builder()
               .http_method(lark.HttpMethod.POST)
               .uri(f"/open-apis/drive/v1/permissions/{doc_id}/members?type=docx")
               .token_types({lark.AccessTokenType.TENANT})
               .body({"member_type": "openid", "member_id": open_id,
                      "perm": "view"})
               .build())
        resp = self.sdk_client.request(req)
        payload = json.loads(resp.raw.content)
        if payload.get("code") != 0:
            raise LarkCLIError(
                f"grant doc view failed: code={payload.get('code')} "
                f"msg={payload.get('msg')}")

    def append_plain_text(self, doc_id: str, text: str,
                          index: int = -1) -> str:
        """追加一段纯文本块。返回新 block_id。

        index=-1（默认）追加到文档末尾；index>=0 插入到根块对应位置
        （锚点定位写入用，仅 SDK 路径支持）。
        """
        if self.sdk_client is not None:
            blocks = [{"block_type": 2, "text": {
                "elements": [{"text_run": {"content": text}}]}}]
            children = self._sdk_create_children(doc_id, blocks, index=index)
            block_id: str = (children[0].get("block_id", "")
                             if children else "")
            return block_id
        result = self.cli.run([
            "docx", "block", "create",
            "--doc-id", doc_id,
            "--block-type", "text",
            "--content", text,
        ])
        block_id = result.get("block_id", "")
        return block_id

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
        payload = json.loads(resp.raw.content)
        if payload.get("code") != 0:
            raise LarkCLIError(
                f"wiki get_node failed: code={payload.get('code')} "
                f"msg={payload.get('msg')}")
        node = payload.get("data", {}).get("node", {})
        if node.get("obj_type") != "docx":
            raise LarkCLIError(
                f"wiki 节点不是云文档（obj_type={node.get('obj_type')}），"
                "请绑定 docx 类型文档")
        obj_token: str = node.get("obj_token", "")
        return obj_token

    # === lark-oapi SDK 直连路径 ===
    def _sdk_request(self, method: lark.HttpMethod, uri: str,
                     body: dict[str, Any] | None = None) -> dict[str, Any]:
        """原始 BaseRequest 调 docx API（tenant token 由 SDK 托管），返回 data 段。"""
        builder = (lark.BaseRequest.builder()
                   .http_method(method)
                   .uri(uri)
                   .token_types({lark.AccessTokenType.TENANT})
                   .queries({"document_revision_id": ["-1"]}))
        if body is not None:
            builder = builder.body(body)
        resp = self.sdk_client.request(builder.build())
        payload = json.loads(resp.raw.content)
        if payload.get("code") != 0:
            raise LarkCLIError(
                f"docx api failed: code={payload.get('code')} "
                f"msg={payload.get('msg')} uri={uri}")
        data: dict[str, Any] = payload.get("data", {})
        return data

    def _sdk_create_children(self, doc_id: str, blocks: list[dict[str, Any]],
                             index: int = -1) -> list[dict[str, Any]]:
        """在文档根块创建子块，返回新建块列表。index=-1 末尾追加，>=0 指定位置。"""
        data = self._sdk_request(
            lark.HttpMethod.POST,
            f"/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
            body={"children": blocks, "index": index},
        )
        children: list[dict[str, Any]] = data.get("children", [])
        return children

    def list_root_children(self, doc_id: str) -> list[dict[str, Any]]:
        """按序列出文档根块的一级子块（锚点定位用，仅 SDK 路径）。"""
        if self.sdk_client is None:
            raise LarkCLIError("list_root_children 需要 SDK 直连通道")
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            uri = (f"/open-apis/docx/v1/documents/{doc_id}"
                   f"/blocks/{doc_id}/children?page_size=500")
            if page_token:
                uri += f"&page_token={page_token}"
            req = (lark.BaseRequest.builder()
                   .http_method(lark.HttpMethod.GET)
                   .uri(uri)
                   .token_types({lark.AccessTokenType.TENANT})
                   .queries({"document_revision_id": ["-1"]})
                   .build())
            resp = self.sdk_client.request(req)
            payload = json.loads(resp.raw.content)
            if payload.get("code") != 0:
                raise LarkCLIError(
                    f"docx list children failed: code={payload.get('code')} "
                    f"msg={payload.get('msg')}")
            data = payload.get("data", {})
            items.extend(data.get("items", []))
            if not data.get("has_more"):
                return items
            page_token = data.get("page_token", "")

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
            payload = json.loads(resp.raw.content)
            if payload.get("code") != 0:
                raise LarkCLIError(
                    f"docx list blocks failed: code={payload.get('code')} "
                    f"msg={payload.get('msg')}")
            data = payload.get("data", {})
            items.extend(data.get("items", []))
            if not data.get("has_more"):
                return items
            page_token = data.get("page_token", "")

    def _to_sdk_blocks(self, block: Any) -> list[dict[str, Any]]:
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

    def _sdk_render_blocks(self, doc_id: str, blocks: list[Any]) -> "str | None":
        """SDK 路径：逐块展开批量追加到文档末尾；返回最后写入块的 id。

        返回值供锚点续写跟随定位（Phase 14 后续：anchor_block_id 补齐）。
        本地图片块（ImageBlock.path）无法进批量 children——需三步插入
        （官方 FAQ），遇则先 flush 当前批再单独插入，保持块序；
        单图失败仅记日志跳过（文本写回不受影响）。
        """
        import logging as _logging

        log = _logging.getLogger(__name__)
        last_block_id = None
        batch: list[dict[str, Any]] = []

        def _flush() -> None:
            nonlocal last_block_id, batch
            if batch:
                created = self._sdk_create_children(doc_id, batch)
                if created:
                    last_block_id = created[-1].get("block_id")
                batch = []

        for block in blocks:
            self._rate_limiter.wait()
            if (getattr(block, "type", "") == "image"
                    and getattr(block, "path", "")):
                _flush()
                try:
                    last_block_id = self.insert_doc_image(
                        doc_id, block.path)
                except Exception as e:
                    log.warning("insert doc image failed (%s): %s",
                                getattr(block, "path", ""), e)
                continue
            batch.extend(self._to_sdk_blocks(block))
            if len(batch) >= 50:  # children 单批上限 50
                _flush()
        _flush()
        return last_block_id

    # === Phase 5 ===
    def render_blocks(self, doc_id: str, blocks: list[Any]) -> "str | None":
        """Phase 5: 渲染块到飞书 doc；SDK 路径返回最后写入块 id（CLI 路径 None）。"""
        if self.sdk_client is not None:
            return self._sdk_render_blocks(doc_id, blocks)
        for block in blocks:
            self._rate_limiter.wait()
            payload = self._to_feishu_payload(block)
            self._post_block(doc_id, payload)
        return None

    def _to_feishu_payload(self, block: Any) -> dict[str, Any]:
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

    def _post_block(self, doc_id: str, payload: dict[str, Any]) -> None:
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
