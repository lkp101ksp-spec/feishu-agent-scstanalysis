"""L0 只读工具：read_doc, read_base, list_drive。"""
from __future__ import annotations

from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


def register_l0_read(
    reg: ToolRegistry, *, doc_adapter, base_adapter, drive_adapter
) -> None:
    """注册 L0 只读工具；adapter 为 None 时跳过对应工具（Phase 12 板块②）。"""
    if doc_adapter is not None:
        reg.register(
            ToolSpec(
                name="read_doc",
                description="读取飞书 doc 块树",
                parameters={
                    "type": "object",
                    "properties": {"doc_id": {"type": "string"}},
                    "required": ["doc_id"],
                },
                risk_level="L0_read",
                handler=lambda doc_id: {"blocks": doc_adapter.read_blocks(doc_id)},
            )
        )
    if base_adapter is not None:
        reg.register(
            ToolSpec(
                name="read_base",
                description="读取飞书 base 记录",
                parameters={
                    "type": "object",
                    "properties": {"app_token": {"type": "string"}},
                    "required": ["app_token"],
                },
                risk_level="L0_read",
                handler=lambda app_token: {
                    "records": base_adapter.list_records(app_token)
                },
            )
        )
    if drive_adapter is not None:
        reg.register(
            ToolSpec(
                name="list_drive",
                description="列 Drive 文件",
                parameters={
                    "type": "object",
                    "properties": {"folder_token": {"type": "string"}},
                },
                risk_level="L0_read",
                handler=lambda folder_token="": {
                    "files": drive_adapter.list_files(folder_token)
                },
            )
        )
