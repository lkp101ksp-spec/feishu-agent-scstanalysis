"""Phase 5 blocks ↔ JSON 序列化（用于模板存储）。"""
from __future__ import annotations

import json
from typing import Any

from orchestrator.blocks.schemas import (
    AnyBlock,
    CalloutBlock,
    CodeBlock,
    DividerBlock,
    EmbedBlock,
    EquationBlock,
    FileBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    MathBlock,
    MermaidBlock,
    QuoteBlock,
    QuoteContainerBlock,
    TableBlock,
    TextBlock,
    VideoBlock,
)


def blocks_to_json(blocks: list[AnyBlock]) -> str:
    """list[Block] → JSON 字符串。"""
    return json.dumps([b.model_dump() for b in blocks], ensure_ascii=False)


def json_to_blocks(s: str) -> list[AnyBlock]:
    """JSON 字符串 → list[Block]。"""
    data = json.loads(s)
    return [_parse_block(b) for b in data]


def parse_blocks(raw) -> list[AnyBlock]:
    """宽松解析 blocks 参数：JSON/Python repr 字符串、dict、list[dict] → list[Block]。

    planner 生成的工具 inputs 统一 str() 强转，经 scheduler 引用替换后
    blocks 是 repr 风格字符串（单引号，非合法 JSON）——write_doc 节点
    路径由此收敛（真机 2026-09-01：append_blocks 不存在 + 字符串形态）。
    """
    import ast

    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            data = json.loads(s)
        except ValueError:
            data = ast.literal_eval(s)
    elif isinstance(raw, dict):
        data = [raw]
    else:
        data = raw or []
    return [_parse_block(b if isinstance(b, dict) else dict(b)) for b in data]


def _parse_block(d: dict[str, Any]) -> AnyBlock:
    t = d.get("type")
    match t:
        case "heading": return HeadingBlock(**d)
        case "text":    return TextBlock(**d)
        case "code":    return CodeBlock(**d)
        case "quote":   return QuoteBlock(**d)
        case "quote_container": return QuoteContainerBlock(**d)
        case "table":   return TableBlock(**d)
        case "list":    return ListBlock(**d)
        case "image":   return ImageBlock(**d)
        case "embed":   return EmbedBlock(**d)
        case "divider": return DividerBlock(**d)
        case "callout": return CalloutBlock(**d)
        case "equation": return EquationBlock(**d)
        case "math":    return MathBlock(**d)
        case "mermaid": return MermaidBlock(**d)
        case "video":   return VideoBlock(**d)
        case "file":    return FileBlock(**d)
        case _: raise ValueError(f"unknown block type: {t}")
