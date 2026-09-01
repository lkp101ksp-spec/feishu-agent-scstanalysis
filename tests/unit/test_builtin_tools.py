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


def test_l0_read_doc_calls_get_block_tree_and_flattens():
    """Phase 12 真机修正：read_doc 走真实 API get_block_tree，附扁平 text。

    输出键用 block_tree 而非 blocks——blocks 是 ToolHandler 的富文本
    保留键，撞名会被误解析炸掉。
    """
    from unittest.mock import MagicMock

    doc = MagicMock()
    doc.get_block_tree.return_value = [
        {"block_type": 2, "text": {"elements": [
            {"text_run": {"content": "标题内容"}},
        ]}},
        {"block_type": 14, "code": {"elements": [
            {"text_run": {"content": "print(1)"}},
        ]}},
    ]
    reg = ToolRegistry()
    register_l0_read(reg, doc_adapter=doc, base_adapter=None, drive_adapter=None)
    out = reg.get("read_doc").handler(doc_id="doccnX")
    doc.get_block_tree.assert_called_with("doccnX")
    assert out["block_tree"] == doc.get_block_tree.return_value
    assert out["text"] == "标题内容\nprint(1)"


def test_l1_registers_three():
    """run_blast stub 已删除（2026-08-31）：真实检索走 L3 blast_search。"""
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=object(), kernel_manager=object())
    names = sorted(t.name for t in reg.list())
    assert names == ["classify_intent", "run_python", "summarize_text"]
    run_py = reg.get("run_python")
    assert run_py.risk_level == "L1_compute"


def test_l1_planner_visibility_all_real():
    """Phase 13 T2：run_python 真沙箱接入对 Planner 开放；stub 清理后全部可见。"""
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=object(), kernel_manager=object())
    visible = {t.name for t in reg.list(planner_visible=True)}
    assert visible == {"classify_intent", "run_python", "summarize_text"}
    schema_names = {
        f["function"]["name"]
        for f in reg.to_openai_functions(planner_visible=True)
    }
    assert schema_names == visible
    assert "run_python" in schema_names  # 描述声明输出字段（T2）


def test_l1_run_python_real_sandbox():
    """Phase 13 T2：run_python 走 KernelPool.exec_code 真执行。"""
    from unittest.mock import MagicMock

    pool = MagicMock()
    pool.exec_code.return_value = {"stdout": "hi\n", "result": "42"}
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=object(), kernel_manager=pool)
    out = reg.get("run_python").handler(code="print('hi')\n42")
    pool.exec_code.assert_called_once_with("research", "print('hi')\n42", timeout_sec=60)
    assert out == {"stdout": "hi\n", "result": "42"}


def test_l1_run_python_timeout_maps_error_code():
    from unittest.mock import MagicMock

    from shared.errors import SandboxTimeoutError

    pool = MagicMock()
    pool.exec_code.side_effect = SandboxTimeoutError("exec exceeded 5s")
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=object(), kernel_manager=pool)
    out = reg.get("run_python").handler(code="while True: pass")
    assert out["error_code"] == "SANDBOX_TIMEOUT"


def test_l1_run_python_unavailable_without_pool():
    """kernel_pool 未注入（引擎未初始化）→ SANDBOX_UNAVAILABLE 不裸抛。"""
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=object(), kernel_manager=None)
    out = reg.get("run_python").handler(code="print(1)")
    assert out["error_code"] == "SANDBOX_UNAVAILABLE"


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


def test_write_doc_handler_parses_repr_string_blocks():
    """write_doc 节点路径：blocks 为 repr 风格字符串 → 解析后走 render_blocks。

    真机 2026-09-01：handler 曾调不存在的 append_blocks（TOOL_EXEC_FAILED），
    且 planner 传入的 blocks 是 str() 强转后的单引号 repr 串。
    """
    from unittest.mock import MagicMock

    from orchestrator.blocks.schemas import HeadingBlock, TextBlock

    doc_adapter = MagicMock()
    doc_adapter.render_blocks.return_value = "blk_last"
    reg = ToolRegistry()
    register_l2_side_effect(
        reg, doc_adapter=doc_adapter, base_adapter=None,
        im_adapter=None, drive_adapter=None,
    )
    blocks_str = ("[{'type': 'heading', 'level': 2, 'text': '结果'}, "
                  "{'type': 'text', 'text': 'BRCA1 全长 1863 aa'}]")
    out = reg.get("write_doc").handler(doc_id="doc1", blocks=blocks_str)
    assert out == "blk_last"
    called = doc_adapter.render_blocks.call_args
    assert called.args[0] == "doc1"
    parsed = called.args[1]
    assert parsed == [
        HeadingBlock(level=2, text="结果"),
        TextBlock(text="BRCA1 全长 1863 aa"),
    ]


def test_write_doc_handler_accepts_json_and_list():
    """blocks 兼容 JSON 字符串 / list[dict] / 单 dict 三种形态。"""
    from unittest.mock import MagicMock

    from orchestrator.blocks.schemas import TextBlock

    doc_adapter = MagicMock()
    reg = ToolRegistry()
    register_l2_side_effect(
        reg, doc_adapter=doc_adapter, base_adapter=None,
        im_adapter=None, drive_adapter=None,
    )
    handler = reg.get("write_doc").handler

    handler(doc_id="d", blocks='[{"type": "text", "text": "a"}]')
    assert doc_adapter.render_blocks.call_args.args[1] == [TextBlock(text="a")]

    handler(doc_id="d", blocks=[{"type": "text", "text": "b"}])
    assert doc_adapter.render_blocks.call_args.args[1] == [TextBlock(text="b")]

    handler(doc_id="d", blocks={"type": "text", "text": "c"})
    assert doc_adapter.render_blocks.call_args.args[1] == [TextBlock(text="c")]
