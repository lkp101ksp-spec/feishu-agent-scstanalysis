"""Webhook payload → IncomingMessage 归一化。"""
import json
import re
from typing import Optional

from shared.errors import FeishuAgentError
from shared.schemas import IncomingMessage


class NormalizeError(FeishuAgentError):
    """webhook payload 归一化失败。"""


_BIND_DOC_RE = re.compile(r"^/bind-doc\s+(\S+?)(?:\s+@\s*(.+?))?\s*$", re.IGNORECASE)
_DOC_URL_RE = re.compile(r"/docx/([A-Za-z0-9]+)")
_WIKI_URL_RE = re.compile(r"/wiki/([A-Za-z0-9]+)")
_MENTION_RE = re.compile(r"@\w+\s*")


def parse_bind_doc_cmd(text: str) -> tuple[Optional[str], Optional[str]]:
    """识别 `/bind-doc <doc_id> [@锚点文字]` 指令。

    参数兼容三种形态：裸 doc_id、/docx/ 文档链接（自动提取 token）、
    /wiki/ 知识库链接（提取后加 "wiki:" 前缀，由 BindDocService 经
    wiki get_node 接口解析成真实 docx document_id）。

    返回 (anchor, doc_id)：是 bind-doc 指令（anchor 为可选写入锚点文字，无则 None）；
    返回 (None, None)：不是。
    """
    m = _BIND_DOC_RE.match(text.strip())
    if not m:
        return None, None
    arg, anchor = m.group(1), m.group(2)
    url_m = _DOC_URL_RE.search(arg)
    if url_m:
        return anchor, url_m.group(1)
    wiki_m = _WIKI_URL_RE.search(arg)
    if wiki_m:
        return anchor, f"wiki:{wiki_m.group(1)}"
    return anchor, arg


def normalize_im_event(payload: dict) -> IncomingMessage:
    """从 im.message.receive_v1 payload 提取 IncomingMessage。

    字段映射：
    - event.sender.sender_id.open_id → sender_open_id
    - event.message.chat_id → chat_id
    - event.message.message_id → message_id
    - event.message.content (JSON) 中的 text 字段 → text
    - 文本中的 @提及 → 剥离

    异常：
    - NormalizeError: 字段缺失、非文本消息、content 非 JSON
    """
    try:
        event = payload["event"]
        message = event["message"]
        sender_open_id = event["sender"]["sender_id"]["open_id"]
        chat_id = message["chat_id"]
        message_id = message["message_id"]
        msg_type = message["message_type"]
    except (KeyError, TypeError) as e:
        raise NormalizeError(f"missing required field: {e}") from e

    if msg_type != "text":
        raise NormalizeError(f"skip non-text message_type={msg_type}")

    try:
        text = json.loads(message["content"])["text"]
    except (json.JSONDecodeError, KeyError) as e:
        raise NormalizeError(f"invalid text content: {e}") from e

    # 剥离 @ 提及：优先按 mentions 里的 key 精确删除（如 "@_user_1"），
    # 避免误伤 /bind-doc 的 @锚点 参数；mentions 缺 key 时回退正则
    mentions = message.get("mentions")
    if mentions:
        stripped = False
        for m in mentions:
            key = m.get("key") if isinstance(m, dict) else getattr(m, "key", None)
            if key and key in text:
                text = text.replace(key, "")
                stripped = True
        if not stripped:
            text = _MENTION_RE.sub("", text)
        text = text.strip()

    # 识别 /bind-doc 指令（可带 @锚点）
    anchor, doc_id = parse_bind_doc_cmd(text)
    is_bind_cmd = doc_id is not None

    return IncomingMessage(
        message_id=message_id,
        chat_id=chat_id,
        sender_open_id=sender_open_id,
        text=text,
        is_bind_doc_cmd=is_bind_cmd,
        bind_doc_id=doc_id,
        bind_anchor=anchor,
    )
