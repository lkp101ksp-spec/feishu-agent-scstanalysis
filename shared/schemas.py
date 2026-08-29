"""Pydantic 数据模型。"""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class IncomingMessage(BaseModel):
    """网关归一化后的入站消息。"""
    message_id: str
    chat_id: str
    sender_open_id: str
    text: str
    app_id: Optional[str] = None
    # 会话类型：p2p 私聊 / group 群聊（ws_client 与 webhook 均透传）
    chat_type: str = ""
    is_bind_doc_cmd: bool = False
    bind_doc_id: Optional[str] = None
    bind_anchor: Optional[str] = None
    # 消息级临时写入锚点（#写到 语法）：仅影响本条消息的写入位置，
    # 优先于会话级 bind_anchor
    write_anchor: Optional[str] = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    estimated_tokens: int = 0
    summary: Optional["SummaryBlock"] = None


class SummaryBlock(BaseModel):
    """上下文压缩后的总结块；可作为 ChatMessage 替代品。"""
    summary_id: str
    text: str
    kind: str = "summary"
    ref: str = ""
    saved_tokens: int = 0


class AuditRecord(BaseModel):
    actor_type: Literal["user", "system", "tool"]
    actor_id: str
    action: str
    target_type: str
    target_id: str
    detail: dict = Field(default_factory=dict)
