"""L2 副作用工具：write_doc, write_base_projection, send_card, upload_drive。"""
from __future__ import annotations

from typing import Any

from orchestrator.blocks.serializer import parse_blocks
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


def register_l2_side_effect(
    reg: ToolRegistry,
    *,
    doc_adapter: Any,
    base_adapter: Any,
    im_adapter: Any,
    drive_adapter: Any,
) -> None:
    """注册 L2 副作用工具；adapter 为 None 时跳过对应工具（Phase 12 板块②）。"""
    if doc_adapter is not None:
        reg.register(
            ToolSpec(
                name="write_doc",
                description=(
                    "追加块列表到飞书 doc（每块形如 "
                    '{"type": "text", "text": "..."}，文本可引用上游输出如'
                    " 'n3.summary'）"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "doc_id": {"type": "string"},
                        "blocks": {"type": "array"},
                    },
                    "required": ["doc_id", "blocks"],
                },
                risk_level="L2_side_effect",
                handler=lambda doc_id, blocks: doc_adapter.render_blocks(
                    doc_id, parse_blocks(blocks)),
                approval_card_template="l2_tool_confirm",
            )
        )
    if base_adapter is not None:
        reg.register(
            ToolSpec(
                name="write_base_projection",
                description="写飞书 Base 投影",
                parameters={
                    "type": "object",
                    "properties": {
                        "app_token": {"type": "string"},
                        "record": {"type": "object"},
                    },
                },
                risk_level="L2_side_effect",
                handler=lambda app_token, record: base_adapter.write_record(
                    app_token, record
                ),
                approval_card_template="l2_tool_confirm",
            )
        )
    if im_adapter is not None:
        reg.register(
            ToolSpec(
                name="send_card",
                description="发送 IM 交互卡片",
                parameters={
                    "type": "object",
                    "properties": {
                        "chat_id": {"type": "string"},
                        "card": {"type": "object"},
                    },
                },
                risk_level="L2_side_effect",
                handler=lambda chat_id, card: im_adapter.send_card(chat_id, card),
                approval_card_template="l2_tool_confirm",
            )
        )
    if drive_adapter is not None:
        reg.register(
            ToolSpec(
                name="upload_drive",
                description="上传文件到 Drive",
                parameters={
                    "type": "object",
                    "properties": {
                        "local_path": {"type": "string"},
                        "mime": {"type": "string"},
                    },
                },
                risk_level="L2_side_effect",
                handler=lambda local_path, mime="application/octet-stream": drive_adapter.upload_from_path(
                    local_path, mime=mime
                ),
                approval_card_template="l2_tool_confirm",
            )
        )
