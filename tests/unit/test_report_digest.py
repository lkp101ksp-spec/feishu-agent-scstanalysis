"""Phase D：section_digest 产物收集与 csv 摘要单测（SimpleNamespace 假 plan/scheduler）。"""
from datetime import UTC, datetime
from pathlib import Path
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


def _host_path(tmp_path: Path, rel: str) -> str:
    """按 container_to_host 同款拼法构造主机路径（只 resolve 存在的根目录，
    避免对不存在文件 resolve 触发 Windows \\\\?\\ 前缀分支抖动误判）。"""
    return str(tmp_path.resolve() / rel)


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
    assert proc.images == [_host_path(tmp_path, "ds1/umap.png")]
    assert proc.numbers == {"n_cells": 8000, "n_genes": 20000}
    assert cnv.title == "CNV 恶性判定与亚克隆"
    assert cnv.images == [_host_path(tmp_path, "ds1/cnv/heatmap.png")]
    assert cnv.csvs == [_host_path(tmp_path, "ds1/cnv/cnv_celltype_summary.csv")]
    assert cnv.numbers["n_malignant"] == 150
    assert cnv.numbers["method"] == "infercnvpy"
    assert cnv.numbers["matched_references"] == ["T cells", "B cells"]
    assert "note" not in cnv.numbers


def test_collect_nested_trajectory_full_outputs(tmp_path):
    """trajectory_full 嵌套 emit（palantir/slingshot 子 dict）下钻收集。

    键加引擎前缀防撞（palantir.n_terminal）；records 列表不进 numbers。
    """
    plan = SimpleNamespace(nodes=[_node("n1", "sc_pseudotime")])
    sch = SimpleNamespace(_handles={
        "n1": _handle(ExecutionState.SUCCESS, {
            "method": "trajectory_full",
            "n_cells": 12899,
            "root_cluster": "2",
            "palantir": {
                "n_terminal": 12,
                "start_cell": "AAACATACAACCAC-1",
                "pseudotime_csv": "/ws/ds/palantir_pt.csv",
                "terminal_csv": "/ws/ds/terminal_states.csv",
                "umap_png": "/ws/ds/palantir_umap.png",
                "branch_umap_png": "/ws/ds/palantir_branch_umap.png",
                "branch_de_csv": "/ws/ds/palantir_branch_de.csv",
                "terminal_states": [{"cell": "x", "leiden": "3"}],
            },
            "slingshot": {
                "n_lineages": 3,
                "lineages": ["lineage1", "lineage2", "lineage3"],
                "slingshot_pt_csv": "/ws/ds/slingshot_pt.csv",
                "umap_png": "/ws/ds/slingshot_umap.png",
            },
        })})
    sections = collect_sections(plan, sch, str(tmp_path))
    sec = sections[0]
    assert sec.numbers["palantir.n_terminal"] == 12
    assert sec.numbers["slingshot.n_lineages"] == 3
    assert sec.numbers["n_cells"] == 12899
    assert sec.numbers["slingshot.lineages"] == ["lineage1", "lineage2",
                                                 "lineage3"]
    for rel in ("ds/palantir_pt.csv", "ds/terminal_states.csv",
                "ds/palantir_branch_de.csv", "ds/slingshot_pt.csv"):
        assert _host_path(tmp_path, rel) in sec.csvs, rel
    for rel in ("ds/palantir_umap.png", "ds/palantir_branch_umap.png",
                "ds/slingshot_umap.png"):
        assert _host_path(tmp_path, rel) in sec.images, rel
    # records 列表（list[dict]）不是短标量，不进 numbers
    assert "palantir.terminal_states" not in sec.numbers


def test_collect_generic_png_and_no_pngs_in_numbers(tmp_path):
    """任意 .png 结尾输出串入图（paga_png/branch_trend_png 等非四键）；
    pngs 列表只进图不重复进 numbers。"""
    plan = SimpleNamespace(nodes=[_node("n1", "sc_pseudotime")])
    sch = SimpleNamespace(_handles={
        "n1": _handle(ExecutionState.SUCCESS, {
            "paga_png": "/ws/ds/paga.png",
            "branch_trend_png": "/ws/ds/branch_trend.png",
            "pngs": ["/ws/ds/dot1.png", "/ws/ds/dot2.png"],
        })})
    sec = collect_sections(plan, sch, str(tmp_path))[0]
    assert sec.images == [
        _host_path(tmp_path, "ds/paga.png"),
        _host_path(tmp_path, "ds/branch_trend.png"),
        _host_path(tmp_path, "ds/dot1.png"),
        _host_path(tmp_path, "ds/dot2.png"),
    ]
    assert "pngs" not in sec.numbers


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
