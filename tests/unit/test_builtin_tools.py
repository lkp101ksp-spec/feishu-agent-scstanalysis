from orchestrator.tools.builtin.l0_read import register_l0_read
from orchestrator.tools.builtin.l1_compute import register_l1_compute
from orchestrator.tools.builtin.l2_side_effect import register_l2_side_effect
from orchestrator.tools.tool_registry import ToolRegistry


def test_l0_registers_three():
    reg = ToolRegistry()
    register_l0_read(
        reg, doc_adapter=object(), base_adapter=object(), drive_adapter=object()
    )
    names = sorted(t.name for t in reg.list())
    assert names == ["list_drive", "read_base", "read_doc"]


def test_l0_none_adapters_skip_tools():
    """Phase 12 板块②：adapter 缺失只注册可用工具，不抛错。"""
    reg = ToolRegistry()
    register_l0_read(reg, doc_adapter=object(), base_adapter=None, drive_adapter=None)
    assert [t.name for t in reg.list()] == ["read_doc"]


def test_l2_none_adapters_skip_tools():
    """Phase 12 板块②：base/drive 缺失时跳过对应 L2 工具。"""
    reg = ToolRegistry()
    register_l2_side_effect(
        reg, doc_adapter=object(), base_adapter=None,
        im_adapter=object(), drive_adapter=None,
    )
    names = sorted(t.name for t in reg.list())
    assert names == ["send_card", "write_doc"]


def test_l1_registers_four():
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=object(), kernel_manager=object())
    names = sorted(t.name for t in reg.list())
    assert names == ["classify_intent", "run_blast", "run_python", "summarize_text"]
    run_py = reg.get("run_python")
    assert run_py.risk_level == "L1_compute"


def test_l1_planner_visibility_hides_stubs():
    """Phase 12 板块④：stub 工具对 Planner 隐藏，真实现可见。"""
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=object(), kernel_manager=object())
    visible = {t.name for t in reg.list(planner_visible=True)}
    assert visible == {"classify_intent", "summarize_text"}
    schema_names = {
        f["function"]["name"]
        for f in reg.to_openai_functions(planner_visible=True)
    }
    assert "run_python" not in schema_names
    assert "run_blast" not in schema_names


def test_l1_summarize_real_llm():
    """Phase 12 板块④：summarize 走真 LLM（mock router），非截断。"""
    from unittest.mock import MagicMock

    llm = MagicMock()
    llm.chat.return_value = "- 要点一\n- 要点二"
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=llm, kernel_manager=None)
    out = reg.get("summarize_text").handler(text="很长的文本" * 100, max_words=50)
    assert out == {"summary": "- 要点一\n- 要点二"}
    assert llm.chat.call_count == 1


def test_l1_summarize_llm_failed_returns_error_code():
    """LLM 失败：返回 error_code=LLM_FAILED（ToolHandler 透传 FAILED）。"""
    from unittest.mock import MagicMock

    from shared.errors import LLMCallError

    llm = MagicMock()
    llm.chat.side_effect = LLMCallError("boom")
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=llm, kernel_manager=None)
    out = reg.get("summarize_text").handler(text="x")
    assert out["error_code"] == "LLM_FAILED"


def test_l1_classify_real_llm_and_fallback():
    """分类走真 LLM；失败回退 other。"""
    from unittest.mock import MagicMock

    llm = MagicMock()
    llm.chat.return_value = "data_analysis"
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=llm, kernel_manager=None)
    assert reg.get("classify_intent").handler(text="帮我分析数据") == {
        "intent": "data_analysis"
    }

    llm2 = MagicMock()
    llm2.chat.side_effect = RuntimeError("down")
    reg2 = ToolRegistry()
    register_l1_compute(reg2, llm_router=llm2, kernel_manager=None)
    assert reg2.get("classify_intent").handler(text="x") == {"intent": "other"}


def test_l2_registers_four():
    reg = ToolRegistry()
    register_l2_side_effect(
        reg,
        doc_adapter=object(),
        base_adapter=object(),
        im_adapter=object(),
        drive_adapter=object(),
    )
    names = sorted(t.name for t in reg.list())
    assert names == [
        "send_card",
        "upload_drive",
        "write_base_projection",
        "write_doc",
    ]
    for t in reg.list():
        assert t.risk_level == "L2_side_effect"
