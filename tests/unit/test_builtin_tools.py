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


def test_l1_registers_four():
    reg = ToolRegistry()
    register_l1_compute(reg, llm_router=object(), kernel_manager=object())
    names = sorted(t.name for t in reg.list())
    assert names == ["classify_intent", "run_blast", "run_python", "summarize_text"]
    run_py = reg.get("run_python")
    assert run_py.risk_level == "L1_compute"


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
