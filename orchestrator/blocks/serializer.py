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
