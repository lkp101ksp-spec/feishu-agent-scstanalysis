"""Webhook payload → IncomingMessage 归一化。"""
import json
import re
from typing import Optional

from shared.errors import FeishuAgentError
from shared.schemas import IncomingMessage


class NormalizeError(FeishuAgentError):
    """webhook payload 归一化失败。"""


_BIND_DOC_RE = re.compile(r"^/bind-doc\s+(\S+)\s*$", re.IGNORECASE)
_DOC_URL_RE = re.compile(r"/docx/([A-Za-z0-9]+)")
_MENTION_RE = re.compile(r"@\w+\s*")


def parse_bind_doc_cmd(text: str) -> tuple[None, Optional[str]]:
    """识别 `/bind-doc <doc_id>` 指令。

    参数兼容两种形态：裸 doc_id（doxcn...）或完整文档链接
    （https://xxx.feishu.cn/docx/<doc_id>?...），链接形态自动提取 token。

    返回 (None, doc_id)：是 bind-doc 指令；
    返回 (None, None)：不是。
    """
    m = _BIND_DOC_RE.match(text.strip())
    if not m:
        return None, None
    arg = m.group(1)
    url_m = _DOC_URL_RE.search(arg)
    return None, (url_m.group(1) if url_m else arg)


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

    # 剥离 @ 提及
    if message.get("mentions"):
        text = _MENTION_RE.sub("", text).strip()

    # 识别 /bind-doc 指令
    _, doc_id = parse_bind_doc_cmd(text)
    is_bind_cmd = doc_id is not None

    return IncomingMessage(
        message_id=message_id,
        chat_id=chat_id,
        sender_open_id=sender_open_id,
        text=text,
        is_bind_doc_cmd=is_bind_cmd,
        bind_doc_id=doc_id,
    )
