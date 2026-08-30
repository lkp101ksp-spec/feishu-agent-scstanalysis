"""L0 只读工具：read_doc, read_base, list_drive。

Phase 12 真机修正（2026-08-30）：read_doc 原调 doc_adapter.read_blocks
——该方法不存在（真实 API 是 get_block_tree），真机首节点必炸
AttributeError。改为 get_block_tree 并附扁平化 text 输出（下游
summarize_text 等 LLM 工具直接消费纯文本）。
"""
from __future__ import annotations

from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


def _flatten_blocks_text(blocks) -> str:
    """递归提取块树里的全部文本 content（read_doc 的 text 输出）。

    SDK/CLI 两种结构通吃：深度遍历 dict/list，收集字符串型 "content" 值。
    """

    def _walk(node, out: list) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "content" and isinstance(v, str) and v:
                    out.append(v)
                else:
                    _walk(v, out)
        elif isinstance(node, list):
            for item in node:
                _walk(item, out)

    parts: list[str] = []
    _walk(blocks, parts)
    return "\n".join(parts)


def register_l0_read(
    reg: ToolRegistry, *, doc_adapter, base_adapter, drive_adapter
) -> None:
    """注册 L0 只读工具；adapter 为 None 时跳过对应工具（Phase 12 板块②）。"""
    if doc_adapter is not None:
        reg.register(
            ToolSpec(
                name="read_doc",
                description=(
                    "读取飞书 doc 内容；输出 text=纯文本正文"
                    "（下游工具用 <node_id>.text 引用）"
                ),
                parameters={
                    "type": "object",
                    "properties": {"doc_id": {"type": "string"}},
                    "required": ["doc_id"],
                },
                risk_level="L0_read",
                # 输出键不能用 blocks——ToolHandler 把 blocks 当 Phase 5
                # 富文本块解析，原始 docx 树会被误解析炸掉（真机 2026-08-30）
                handler=lambda doc_id: {
                    "block_tree": doc_adapter.get_block_tree(doc_id),
                    "text": _flatten_blocks_text(
                        doc_adapter.get_block_tree(doc_id)
                    ),
                },
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
