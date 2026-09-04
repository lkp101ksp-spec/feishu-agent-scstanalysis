"""ToolHandler 对 handler 返回 ToolResult 的透传单测（Phase 27 修复）。

背景：skill 工具 handler（skill_loader._make_handler）直接返回 ToolResult，
此前 execute 走 dict 包装分支把 ToolResult 包进 {"result": repr}，
error_code 丢失导致 AgentLoop 误判成功、连败禁用不触发。
修复后：ToolResult 原样透传，保留 error_code/error_message/outputs。
"""
from orchestrator.tools.tool_handler import ToolHandler, ToolResult
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


def _reg_with(handler) -> ToolRegistry:
    """注册单个 L0 工具（绕过 AST/coerce 干扰，直测返回包装逻辑）。"""
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="skill_demo",
            description="skill 子进程模拟",
            parameters={"type": "object"},
            risk_level="L0_read",
            handler=handler,
        )
    )
    return reg


def test_toolresult_error_passthrough_keeps_error_code():
    """handler 返回 error ToolResult → error_code/message 透传不丢。"""
    reg = _reg_with(
        lambda **kw: ToolResult(
            outputs={}, artifacts_ids=[],
            error_code="SCRIPT_ERROR", error_message="exit 1: boom",
        )
    )
    out = ToolHandler(registry=reg).execute("skill_demo", {})
    assert out.error_code == "SCRIPT_ERROR"
    assert out.error_message == "exit 1: boom"
    assert out.outputs == {}


def test_toolresult_success_passthrough_keeps_outputs():
    """handler 返回成功 ToolResult → outputs 原样透传（不包 result repr）。"""
    reg = _reg_with(
        lambda **kw: ToolResult(
            outputs={"stdout": "report generated"}, artifacts_ids=[],
            error_code="", error_message="",
        )
    )
    out = ToolHandler(registry=reg).execute("skill_demo", {})
    assert not out.error_code
    assert out.outputs.get("stdout") == "report generated"
    assert "result" not in out.outputs
