"""Phase 16 T2：工具 ACL（禁用名单）单测。

覆盖三层：
1. parse_disabled_tools 解析
2. ToolHandler 执行层兜底（TOOL_DISABLED）
3. settings.disabled_tools 配置项默认值
"""
from types import SimpleNamespace

from orchestrator.tools.tool_handler import ToolHandler
from orchestrator.tools.tool_registry import (
    ToolRegistry,
    ToolSpec,
    parse_disabled_tools,
)


def _reg_with_echo() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="echo",
            description="echo arg",
            parameters={"type": "object"},
            risk_level="L0_read",
            handler=lambda **kw: {"result": kw.get("text")},
        )
    )
    return reg


# === parse_disabled_tools 解析 ===

def test_parse_disabled_tools_basic():
    """逗号分隔 → 集合（strip + 去空项）。"""
    assert parse_disabled_tools(" blast_search, run_python ") == {
        "blast_search", "run_python"
    }


def test_parse_disabled_tools_empty():
    """空串/None/全空白 → 空集（不禁用，与现状一致）。"""
    assert parse_disabled_tools("") == set()
    assert parse_disabled_tools(None) == set()
    assert parse_disabled_tools("  , , ") == set()


# === ToolHandler 执行层兜底 ===

def test_execute_disabled_tool_returns_tool_disabled():
    """禁用名单内工具执行 → TOOL_DISABLED，handler 不被调用。"""
    reg = _reg_with_echo()
    settings = SimpleNamespace(disabled_tools="echo")
    handler = ToolHandler(registry=reg, settings=settings)
    out = handler.execute("echo", {"text": "hi"})
    assert out.error_code == "TOOL_DISABLED"
    assert "disabled" in out.error_message


def test_execute_enabled_tool_passes():
    """名单外/空名单 → 正常执行。"""
    reg = _reg_with_echo()
    # 空名单
    h1 = ToolHandler(registry=reg, settings=SimpleNamespace(disabled_tools=""))
    assert h1.execute("echo", {"text": "hi"}).outputs == {"result": "hi"}
    # 名单里是别的工具
    h2 = ToolHandler(
        registry=reg, settings=SimpleNamespace(disabled_tools="blast_search")
    )
    assert h2.execute("echo", {"text": "hi"}).outputs == {"result": "hi"}


def test_execute_without_settings_passes():
    """未注入 settings（旧测试/旧装配路径）→ 不做 ACL 校验，行为不变。"""
    reg = _reg_with_echo()
    handler = ToolHandler(registry=reg)
    out = handler.execute("echo", {"text": "hi"})
    assert out.error_code is None
    assert out.outputs == {"result": "hi"}
