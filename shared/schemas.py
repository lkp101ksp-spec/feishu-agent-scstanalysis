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
    is_bind_doc_cmd: bool = False
    bind_doc_id: Optional[str] = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class AuditRecord(BaseModel):
    actor_type: Literal["user", "system", "tool"]
    actor_id: str
    action: str
    target_type: str
    target_id: str
    detail: dict = Field(default_factory=dict)