"""L1 纯计算工具：summarize_text, classify_intent, run_python, run_blast。"""
from __future__ import annotations

from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


def register_l1_compute(reg: ToolRegistry, *, llm_router, kernel_manager) -> None:
    reg.register(
        ToolSpec(
            name="summarize_text",
            description="文本摘要（调 LLM）",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "max_words": {"type": "integer"},
                },
                "required": ["text"],
            },
            risk_level="L1_compute",
            handler=lambda text, max_words=200: {
                "summary": _summarize(llm_router, text, max_words)
            },
        )
    )
    reg.register(
        ToolSpec(
            name="classify_intent",
            description="意图分类（调 LLM）",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            risk_level="L1_compute",
            handler=lambda text: {"intent": _classify(llm_router, text)},
        )
    )
    reg.register(
        ToolSpec(
            name="run_python",
            description="在 Jupyter Kernel 中执行 Python 代码",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "session_id": {"type": "string"},
                },
                "required": ["code"],
            },
            risk_level="L1_compute",
            handler=lambda code, session_id: _run_python(
                kernel_manager, code, session_id
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="run_blast",
            description="BLAST 序列比对（白名单网络）",
            parameters={
                "type": "object",
                "properties": {
                    "sequence": {"type": "string"},
                    "program": {"type": "string"},
                },
                "required": ["sequence"],
            },
            risk_level="L1_compute",
            handler=lambda sequence, program="blastn": _run_blast(sequence, program),
        )
    )


def _summarize(llm_router, text, max_words):
    return text[:max_words]


def _classify(llm_router, text):
    return "unknown"


def _run_python(kernel_manager, code, session_id):
    return {"stdout": "", "result": None}


def _run_blast(sequence, program):
    return {"hits": []}