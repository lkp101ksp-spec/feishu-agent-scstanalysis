"""Phase D：section_digest 产物收集与 csv 摘要单测（SimpleNamespace 假 plan/scheduler）。"""
from datetime import UTC, datetime
from types import SimpleNamespace

from orchestrator.report.section_digest import (
    CSV_HEAD_LINES,
    DIGEST_LIMIT,
    SECTION_TITLES,
    collect_sections,
    csv_digest,
    section_digest_text,
    truncate_middle,
)
from shared.executor_types import ExecutionState, TaskHandle


def _node(node_id: str, tool: str) -> SimpleNamespace:
    return SimpleNamespace(node_id=node_id, kind="tool", tool_name=tool)


def _handle(state: ExecutionState, outputs: dict | None) -> TaskHandle:
    return TaskHandle(
        execution_id="e1", task_id="t", node_id="n1", state=state,
        started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
        outputs=outputs)


def _plan_and_sched() -> tuple[SimpleNamespace, SimpleNamespace]:
    plan = SimpleNamespace(nodes=[
        _node("n1", "sc_process"),
        _node("n2", "summarize_text"),
        _node("n3", "sc_cnv"),
        _node("n4", "sc_unknown_new"),
    ])
    sch = SimpleNamespace(_handles={
        "n1": _handle(ExecutionState.SUCCESS, {
            "umap_png": "/ws/ds1/umap.png",
            "n_cells": 8000, "n_genes": 20000,
        }),
        "n2": _handle(ExecutionState.SUCCESS, {"summary": "- 要点"}),
        "n3": _handle(ExecutionState.SUCCESS, {
            "pngs": ["/ws/ds1/cnv/heatmap.png"],
            "summary_csv": "/ws/ds1/cnv/cnv_celltype_summary.csv",
            "n_malignant": 150, "malignant_ratio": 0.385,
            "method": "infercnvpy",
            "matched_references": ["T cells", "B cells"],
            "note": "/ws/ds1/cnv/x",  # 路径样字符串不进 numbers
        }),
        "n4": _handle(ExecutionState.FAILED, {"umap_png": "/ws/ds1/x.png"}),
    })
    return plan, sch


def test_collect_only_successful_sc_nodes(tmp_path):
    plan, sch = _plan_and_sched()
    sections = collect_sections(plan, sch, str(tmp_path))
    assert [s.tool_name for s in sections] == ["sc_process", "sc_cnv"]


def test_collect_artifact_classification(tmp_path):
    plan, sch = _plan_and_sched()
    sections = collect_sections(plan, sch, str(tmp_path))
    proc, cnv = sections
    assert proc.title == "数据质控与预处理"
    assert proc.images == [str((tmp_path / "ds1" / "umap.png").resolve())]
    assert proc.numbers == {"n_cells": 8000, "n_genes": 20000}
    assert cnv.title == "CNV 恶性判定与亚克隆"
    assert cnv.images == [str((tmp_path / "ds1" / "cnv" / "heatmap.png").resolve())]
    assert cnv.csvs == [str(
        (tmp_path / "ds1" / "cnv" / "cnv_celltype_summary.csv").resolve())]
    assert cnv.numbers["n_malignant"] == 150
    assert cnv.numbers["method"] == "infercnvpy"
    assert cnv.numbers["matched_references"] == ["T cells", "B cells"]
    assert "note" not in cnv.numbers


def test_unknown_tool_falls_back_to_tool_name(tmp_path):
    plan = SimpleNamespace(nodes=[_node("n1", "sc_newtool")])
    sch = SimpleNamespace(_handles={
        "n1": _handle(ExecutionState.SUCCESS, {"n": 1})})
    sections = collect_sections(plan, sch, str(tmp_path))
    assert sections[0].title == "sc_newtool"


def test_csv_digest_head_lines(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("\n".join(f"r{i},v{i}" for i in range(40)), encoding="utf-8")
    text = csv_digest(str(p))
    assert len(text.splitlines()) == CSV_HEAD_LINES
    assert text.splitlines()[0] == "r0,v0"


def test_csv_digest_missing_file_returns_empty(tmp_path):
    assert csv_digest(str(tmp_path / "nope.csv")) == ""


def test_truncate_middle_keeps_head_and_tail():
    text = "h" * 3000 + "m" * 3000 + "t" * 3000
    out = truncate_middle(text, DIGEST_LIMIT)
    assert len(out) <= DIGEST_LIMIT + 60  # 截断标记本身占字符
    assert out.startswith("h" * 100)
    assert out.endswith("t" * 100)
    assert "截断" in out


def test_truncate_middle_short_text_passthrough():
    assert truncate_middle("abc") == "abc"


def test_section_digest_text_combines_numbers_and_csv(tmp_path):
    plan, sch = _plan_and_sched()
    sections = collect_sections(plan, sch, str(tmp_path))
    cnv = sections[1]
    csv_path = tmp_path / "ds1" / "cnv" / "cnv_celltype_summary.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("celltype,n\nT,10", encoding="utf-8")
    text = section_digest_text(cnv)
    assert "n_malignant=150" in text
    assert "cnv_celltype_summary.csv" in text
    assert "celltype,n" in text


def test_section_titles_cover_all_registered_bio_tools():
    """SECTION_TITLES 覆盖全部已注册 sc_/st_ 工具，新工具漏映射即红。"""
    from unittest.mock import MagicMock

    from orchestrator.tools.builtin.l3_singlecell import register_l3_singlecell
    from orchestrator.tools.builtin.l3_spatial import register_l3_spatial
    from orchestrator.tools.tool_registry import ToolRegistry

    registry = ToolRegistry()
    register_l3_singlecell(registry, MagicMock())
    register_l3_spatial(registry, MagicMock())
    names = {s.name for s in registry.list()
             if s.name.startswith(("sc_", "st_"))}
    missing = names - set(SECTION_TITLES)
    assert not missing, f"缺少章节标题映射: {sorted(missing)}"


def test_section_titles_cover_all_sc_tools(tmp_path):
    """SECTION_TITLES 覆盖全部已注册 sc_/st_ 工具（防章节标题回退为工具名）。"""
    from unittest.mock import MagicMock

    from orchestrator.tools.builtin.l3_singlecell import register_l3_singlecell
    from orchestrator.tools.tool_registry import ToolRegistry

    runner = SimpleNamespace(run=MagicMock(return_value={"ok": True}),
                             resolve_data_path=MagicMock())
    reg = ToolRegistry()
    register_l3_singlecell(reg, runner)
    names = {s.name for s in reg.list() if s.name.startswith(("sc_", "st_"))}
    missing = names - set(SECTION_TITLES)
    assert not missing, f"SECTION_TITLES 缺映射: {sorted(missing)}"
