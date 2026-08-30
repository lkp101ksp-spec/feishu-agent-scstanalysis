"""L1 纯计算工具：summarize_text, classify_intent, run_python, run_blast。

Phase 12 板块④：summarize_text/classify_intent 接真 LLM。
Phase 13 T2：run_python 接真沙箱（KernelPool.exec_code 容器内执行），
对 Planner 开放；run_blast 维持 stub 隐藏（网络版 blast_search 已可用）。
"""
from __future__ import annotations

from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec

# 摘要角色词：约束输出长度与风格，降低不可控性
_SUMMARY_SYSTEM = (
    "你是科研助理。把用户给的文本压缩成要点摘要："
    "中文输出，不超过 max_words 词，按「- 」列点，不添加原文没有的信息。"
)
# 分类角色词：输出单一类别标签
_CLASSIFY_SYSTEM = (
    "你是意图分类器。从这些类别中选恰好一个作为输出（只输出标签本身）："
    "literature_search / data_analysis / doc_writing / experiment_design / "
    "summarization / other"
)


def register_l1_compute(
    reg: ToolRegistry, *, llm_router, kernel_manager,
    exec_timeout_sec: int = 60,
) -> None:
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
            handler=_make_summarize_handler(llm_router),
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
            handler=_make_classify_handler(llm_router),
        )
    )
    reg.register(
        ToolSpec(
            name="run_python",
            description=(
                "在隔离沙箱执行 Python（numpy/pandas/matplotlib 可用，断网只读）。"
                "输出 stdout=打印内容、result=末表达式值"
                "（下游工具用 <node_id>.result 引用）"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码（顶层最后一个"
                        "纯表达式的值会作为 result 返回）",
                    },
                    "session_id": {"type": "string"},
                },
                "required": ["code"],
            },
            risk_level="L1_compute",
            visible_to_planner=True,  # T2 真实沙箱执行已接入
            handler=_make_run_python_handler(
                kernel_manager, exec_timeout_sec
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="run_blast",
            description="BLAST 序列比对（尚未接入沙箱，暂不可规划；请用 blast_search）",
            parameters={
                "type": "object",
                "properties": {
                    "sequence": {"type": "string"},
                    "program": {"type": "string"},
                },
                "required": ["sequence"],
            },
            risk_level="L1_compute",
            visible_to_planner=False,  # stub：已有真实 blast_search 替代
            handler=lambda sequence, program="blastn": _run_blast(sequence, program),
        )
    )


def _make_summarize_handler(llm_router):
    """summarize_text handler 工厂：LLM 失败返回 error_code=LLM_FAILED。

    ToolHandler 会把 dict 内的 error_code/error_message 透传到 ToolResult，
    Scheduler 置节点 FAILED 并向下游传播 SKIPPED（不崩整个计划）。
    text 非 str（如上游 records 列表）时 JSON 序列化——真机 2026-08-30。
    """
    import json

    from shared.schemas import ChatMessage

    def handler(text, max_words=200):
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        try:
            out = llm_router.chat([
                ChatMessage(role="system", content=_SUMMARY_SYSTEM),
                ChatMessage(
                    role="user",
                    content=f"max_words={max_words}\n待摘要文本：\n{text}",
                ),
            ])
        except Exception as e:
            return {"error_code": "LLM_FAILED", "error_message": str(e)}
        return {"summary": out}

    return handler


def _make_classify_handler(llm_router):
    """classify_intent handler 工厂：失败回退 "other"（分类非关键路径）。"""
    from shared.schemas import ChatMessage

    def handler(text):
        try:
            out = llm_router.chat([
                ChatMessage(role="system", content=_CLASSIFY_SYSTEM),
                ChatMessage(role="user", content=text),
            ])
        except Exception:
            return {"intent": "other"}
        # 取首个非空行，剥掉模型可能附带的标点/前后缀
        label = out.strip().splitlines()[0].strip(" .。")
        return {"intent": label or "other"}

    return handler


def _make_run_python_handler(kernel_pool, exec_timeout_sec: int = 60):
    """run_python handler 工厂：KernelPool.exec_code 容器内真实执行。

    session_id 缺省 "research"——模型不规划 session_id，研究任务内
    共享一个容器（池复用）。错误经 dict error_code 透传（ToolHandler
    置节点 FAILED 并向下游传播 SKIPPED）。
    """
    from shared.errors import SandboxTimeoutError, SandboxUnavailableError

    def handler(code, session_id=""):
        if kernel_pool is None:
            return {
                "error_code": "SANDBOX_UNAVAILABLE",
                "error_message": "kernel_pool 未注入（引擎未初始化）",
            }
        try:
            return kernel_pool.exec_code(
                session_id or "research", code,
                timeout_sec=exec_timeout_sec,
            )
        except SandboxTimeoutError as e:
            return {"error_code": "SANDBOX_TIMEOUT", "error_message": str(e)}
        except SandboxUnavailableError as e:
            return {
                "error_code": "SANDBOX_UNAVAILABLE", "error_message": str(e),
            }

    return handler


def _run_blast(sequence, program):
    return {"hits": []}
