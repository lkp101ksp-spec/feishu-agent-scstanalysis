"""L3 领域工具注册（BLAST 等）。

Phase 4 MVP：仅注册 BLAST 工具。
"""
from __future__ import annotations

from orchestrator.tools.bio.blast_ncbi import BlastNCBITool
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


def register_l3_bio(registry: ToolRegistry) -> None:
    """注册 L3 领域工具。Phase 4 MVP 仅含 BLAST。"""
    blast = BlastNCBITool()
    registry.register(ToolSpec(
        name="blast_search",
        description=(
            "BLAST 搜索 NCBI 数据库。"
            "输出 records=命中记录列表（title/summary/length，"
            "下游工具用 <node_id>.records 引用）、ids、total_count。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索词（如 'BRCA1[Gene] AND Homo sapiens[Organism]'）",
                },
                "database": {
                    "type": "string",
                    "default": "protein",
                    "description": (
                        "NCBI Entrez 数据库名（protein/nucleotide/gene/pubmed，"
                        "默认 protein）"
                    ),
                },
                "max_hits": {
                    "type": "integer",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["query"],
        },
        risk_level="L0_read",
        handler=lambda **inputs: blast.handle(**inputs),
    ))
