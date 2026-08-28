"""评论 API 客户端（lark-oapi SDK tenant 直连，ADR-0032）。

SDK 原始请求模式（对齐 doc_adapter._sdk_request 惯例，tenant token 自动刷新）。
官方结构（reply_list.replies[].content.elements 富文本）经 _to_flat 适配为
下游 Phase 8 服务消费的扁平 dict；列表为单页拉取（分页见 ADR-0019 推迟项）。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import lark_oapi as lark

from shared.errors import FeishuAgentError

if TYPE_CHECKING:
    from orchestrator.tools.bio.rate_limiter import RateLimiter


class _AttrWrap:
    """dict → 属性访问包装（适配层统一按属性读，测试可传 MagicMock/对象）。"""

    def __init__(self, d: dict | None) -> None:
        self._d = d or {}

    def __getattr__(self, name: str):
        v = self._d.get(name)
        if isinstance(v, dict):
            return _AttrWrap(v)
        if isinstance(v, list):
            return [_AttrWrap(x) if isinstance(x, dict) else x for x in v]
        return v


def _extract_text(reply: Any) -> str:
    """从 reply.content.elements 拼接纯文本。

    真机 element.type 为 "text_run"（2026-08-28 真机验证）；
    兼容旧假设 "text"。仅拼接有 text_run.text 的片段。
    """
    content = getattr(reply, "content", None)
    elements = getattr(content, "elements", None) or []
    parts = []
    for el in elements:
        if getattr(el, "type", None) in ("text", "text_run"):
            run = getattr(el, "text_run", None)
            if run is not None and getattr(run, "text", None):
                parts.append(run.text)
    return "".join(parts)


def _to_flat(fc: Any) -> dict:
    """官方 FileComment（属性对象）→ 下游扁平 dict；root 正文取 replies[0]。"""
    reply_list = getattr(fc, "reply_list", None)
    replies = list(getattr(reply_list, "replies", None) or []) \
        if reply_list is not None else []
    first = replies[0] if replies else None
    return {
        "comment_id": getattr(fc, "comment_id", None) or "",
        "user_id": getattr(fc, "user_id", None) or "",
        "user_name": "",
        "text": _extract_text(first) if first is not None else "",
        "resolved": bool(getattr(fc, "is_solved", False)),
        "block_id": None,  # 官方 list 接口不返回 block_id
        "replies": [
            {"reply_id": getattr(r, "reply_id", None) or "",
             "user_id": getattr(r, "user_id", None) or "",
             "user_name": "",
             "text": _extract_text(r)}
            for r in replies[1:]
        ],
    }


class CommentClient:
    """评论 API 客户端：列表（扁平适配）+ 回复写回（ADR-0034 回执用）。"""

    def __init__(self, *, sdk_client, rate_limiter: "RateLimiter") -> None:
        self.sdk = sdk_client
        self.rate_limiter = rate_limiter

    def _request(self, method, uri: str, *, queries: dict | None = None,
                 body: dict | None = None) -> dict:
        """BaseRequest 直调评论 API（tenant token 由 SDK 托管），返回 data 段。"""
        builder = (lark.BaseRequest.builder()
                   .http_method(method)
                   .uri(uri)
                   .token_types({lark.AccessTokenType.TENANT}))
        if queries:
            builder = builder.queries(queries)
        if body is not None:
            builder = builder.body(body)
        self.rate_limiter.wait()
        resp = self.sdk.request(builder.build())
        payload = json.loads(resp.raw.content)
        if payload.get("code") != 0:
            raise FeishuAgentError(
                f"comment api failed: code={payload.get('code')} "
                f"msg={payload.get('msg')} uri={uri}")
        return payload.get("data", {})

    def list_comments(self, *, doc_id: str) -> list[dict]:
        """列出文档全部评论（单页）；适配为下游扁平 dict。"""
        data = self._request(
            lark.HttpMethod.GET,
            f"/open-apis/drive/v1/files/{doc_id}/comments",
            queries={"file_type": ["docx"], "user_id_type": ["open_id"]},
        )
        items = data.get("items", []) or []
        return [_to_flat(_AttrWrap(fc)) for fc in items]

    def list_block_comments(self, *, doc_id: str, block_id: str) -> list[dict]:
        """列出指定块的评论（list 接口无 block_id，本地过滤恒空，保留兼容）。"""
        return [c for c in self.list_comments(doc_id=doc_id)
                if c.get("block_id") == block_id]

    def reply_comment(self, *, file_token: str, comment_id: str,
                      text: str) -> dict:
        """在指定评论下回复纯文本（回执写回，ADR-0034）。"""
        data = self._request(
            lark.HttpMethod.POST,
            f"/open-apis/drive/v1/files/{file_token}/comments/{comment_id}/replies",
            queries={"file_type": ["docx"], "user_id_type": ["open_id"]},
            body={"content": {"elements": [
                {"type": "text_run", "text_run": {"text": text}},
            ]}},
        )
        # 真机：reply 对象直接在 data 顶层，无 reply 包裹（2026-08-28 验证）
        reply = data.get("reply") or data
        return {"reply_id": reply.get("reply_id", "")}
