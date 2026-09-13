"""Phase 20：sc_* 5 工具注册单测（fake BioRunner，不起容器）。"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from orchestrator.tools.bio.bio_runner import BioRunError
from orchestrator.tools.builtin.l3_singlecell import register_l3_singlecell
from orchestrator.tools.tool_registry import ToolRegistry


def _registry(tmp_path):
    """fake runner + 注册后的 registry；runner.run 返回可控输出。"""
    runner = SimpleNamespace(
        run=MagicMock(return_value={
            "ok": True, "dataset_ref": "abc123", "n_cells": 100}),
        resolve_data_path=MagicMock(),
    )
    reg = ToolRegistry()
    register_l3_singlecell(reg, runner)
    return reg, runner


def test_register_five_sc_tools(tmp_path):
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    for name in ("sc_load", "sc_qc", "sc_process", "sc_markers", "sc_plot"):
        assert name in names
    # 全部 L1_compute（可规划、无副作用）
    for name in ("sc_load", "sc_qc", "sc_process", "sc_markers", "sc_plot"):
        assert reg.get(name).risk_level == "L1_compute"


def test_sc_load_passes_mount_and_pops_ok(tmp_path):
    reg, runner = _registry(tmp_path)
    data_file = tmp_path / "pbmc.h5ad"
    data_file.write_bytes(b"x" * 16)
    runner.resolve_data_path.return_value = (
        str(tmp_path), "pbmc.h5ad", str(data_file))
    runner.run.return_value = {
        "ok": True, "dataset_ref": "abc123", "n_cells": 500, "n_genes": 2000}

    out = reg.get("sc_load").handler(path=str(data_file))

    runner.resolve_data_path.assert_called_once_with(str(data_file))
    _, kwargs = runner.run.call_args
    assert kwargs["mounts"] == [(str(tmp_path), "/data")]
    assert "ok" not in out
    assert out["dataset_ref"] == "abc123"


def test_sc_load_path_forbidden(tmp_path):
    reg, runner = _registry(tmp_path)
    runner.resolve_data_path.side_effect = BioRunError(
        "SC_PATH_FORBIDDEN", "path outside allowed roots")

    out = reg.get("sc_load").handler(path="E:/secret/x.h5ad")

    assert out == {
        "error_code": "SC_PATH_FORBIDDEN",
        "error_message": "path outside allowed roots",
    }


def test_sc_load_passthrough_existing_dataset_ref(tmp_path):
    """path 恰为既有 dataset_ref（workspace 下目录名）→ 直通返回，不进容器。"""
    reg, runner = _registry(tmp_path)
    ws = tmp_path / "ws"
    (ws / "f1e89bf88edc").mkdir(parents=True)
    runner.workspace_root = str(ws)

    out = reg.get("sc_load").handler(path="f1e89bf88edc")

    assert out["dataset_ref"] == "f1e89bf88edc"
    runner.run.assert_not_called()
    runner.resolve_data_path.assert_not_called()


def test_sc_qc_forwards_params(tmp_path):
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "n_cells_before": 100,
        "n_cells_after": 90}
    out = reg.get("sc_qc").handler(
        dataset_ref="d", min_genes=500, min_cells=5, max_mt_pct=10.0)
    args = runner.run.call_args.args
    assert args[0] == "qc"
    assert args[1]["min_genes"] == 500
    assert args[1]["min_cells"] == 5
    assert args[1]["max_mt_pct"] == 10.0
    assert "ok" not in out


def test_sc_process_timeout_error(tmp_path):
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError("SC_TIMEOUT", "exceeded 900s")
    out = reg.get("sc_process").handler(dataset_ref="d")
    assert out["error_code"] == "SC_TIMEOUT"


def test_sc_plot_schema_limits_genes(tmp_path):
    reg, _ = _registry(tmp_path)
    props = reg.get("sc_plot").parameters["properties"]
    assert props["genes"]["maxItems"] == 6
    assert set(reg.get("sc_plot").parameters["required"]) == {
        "dataset_ref", "genes"}


# === Phase 25：GPU 镜像分流 ===

def _gpu_registry(tmp_path, bio_use_gpu):
    """带 GPU 开关的注册 fixture（本区用例专用）。"""
    runner = SimpleNamespace(
        run=MagicMock(return_value={
            "ok": True, "dataset_ref": "abc123", "n_cells": 100}),
        resolve_data_path=MagicMock(),
    )
    reg = ToolRegistry()
    register_l3_singlecell(reg, runner, bio_use_gpu=bio_use_gpu,
                           bio_gpu_image="bio:gpu-test")
    return reg, runner


def test_sc_process_uses_gpu_image_when_enabled(tmp_path):
    """bio_use_gpu=True → sc_process 以 GPU 镜像 + gpus=True 调 BioRunner。"""
    reg, runner = _gpu_registry(tmp_path, bio_use_gpu=True)
    reg.get("sc_process").handler(dataset_ref="abc123")
    kw = runner.run.call_args.kwargs
    assert kw["image"] == "bio:gpu-test"
    assert kw["gpus"] is True


def test_sc_markers_uses_gpu_image_when_enabled(tmp_path):
    """bio_use_gpu=True → sc_markers 同样走 GPU 镜像。"""
    reg, runner = _gpu_registry(tmp_path, bio_use_gpu=True)
    reg.get("sc_markers").handler(dataset_ref="abc123")
    kw = runner.run.call_args.kwargs
    assert kw["image"] == "bio:gpu-test"
    assert kw["gpus"] is True


def test_sc_process_default_cpu_when_disabled(tmp_path):
    """bio_use_gpu=False（默认）→ image=None（用 runner 默认）+ gpus=False。"""
    reg, runner = _gpu_registry(tmp_path, bio_use_gpu=False)
    reg.get("sc_process").handler(dataset_ref="abc123")
    kw = runner.run.call_args.kwargs
    assert kw["image"] is None
    assert kw["gpus"] is False


def test_sc_qc_never_uses_gpu(tmp_path):
    """sc_qc 不受 GPU 开关影响（I/O 型步骤无 GPU 收益）。"""
    reg, runner = _gpu_registry(tmp_path, bio_use_gpu=True)
    reg.get("sc_qc").handler(dataset_ref="abc123")
    kw = runner.run.call_args.kwargs
    assert kw.get("image") is None
    assert kw.get("gpus") is False


# === Phase 31：富集分析 ===


def test_sc_enrichment_registered_l1(tmp_path):
    """sc_enrichment 注册可见且 L1_compute（研究链路可规划）。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    assert "sc_enrichment" in names
    assert reg.get("sc_enrichment").risk_level == "L1_compute"


def test_sc_enrichment_forwards_params(tmp_path):
    """handler 转发 enrichment 脚本参数（默认基因集三件 + 阈值）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "group": "3",
        "ora": {"hallmark": []}, "gsea": {"hallmark": []},
        "ora_png": "/ws/d/enrichment/ora_bar.png"}
    out = reg.get("sc_enrichment").handler(
        dataset_ref="d", group="3", top_n=10, min_log2fc=0.5)
    args = runner.run.call_args.args
    assert args[0] == "enrichment"
    assert args[1]["dataset_id"] == "d"
    assert args[1]["group"] == "3"
    assert args[1]["gene_sets"] == ["hallmark", "go_bp", "kegg"]
    assert args[1]["top_n"] == 10
    assert args[1]["min_log2fc"] == 0.5
    assert "ok" not in out
    assert out["ora_png"].endswith("ora_bar.png")


def test_sc_enrichment_error_passthrough(tmp_path):
    """BioRunError → error_code 透传（镜像未重建时 SC_SCRIPT_ERROR 可见）。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR", "FileNotFoundError: /opt/gene_sets/kegg.json")
    out = reg.get("sc_enrichment").handler(dataset_ref="d")
    assert out["error_code"] == "SC_SCRIPT_ERROR"
    assert "gene_sets" in out["error_message"]


def test_sc_enrichment_schema_enum(tmp_path):
    """基因集参数 enum 锁定白名单别名（人源三库+小鼠两库），防 planner 幻觉库名。"""
    reg, _ = _registry(tmp_path)
    props = reg.get("sc_enrichment").parameters["properties"]
    assert set(props["gene_sets"]["items"]["enum"]) == {
        "hallmark", "go_bp", "kegg", "kegg_mouse", "wikipathways_mouse"}
    assert reg.get("sc_enrichment").timeout_sec == 1800


# === Phase 32：常用分析（打分/代谢/拟时序） ===


def test_sc_phase32_tools_registered_l1(tmp_path):
    """三新工具注册可见且 L1_compute（研究链路可规划）。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    for name in ("sc_score_genes", "sc_metabolism", "sc_pseudotime"):
        assert name in names
        assert reg.get(name).risk_level == "L1_compute"


def test_sc_score_genes_forwards_params(tmp_path):
    """handler 原样转发 gene_sets 字典（多基因集一次调用）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "n_cells": 100,
        "gene_sets": [{"name": "Cyto", "n_used": 2}], "skipped": []}
    gs = {"Cyto": ["GZMB", "PRF1"], "Exh": ["PDCD1"]}
    out = reg.get("sc_score_genes").handler(dataset_ref="d", gene_sets=gs)
    args = runner.run.call_args.args
    assert args[0] == "score"
    assert args[1]["gene_sets"] == gs
    assert "ok" not in out
    assert out["gene_sets"][0]["name"] == "Cyto"


def test_sc_score_genes_error_passthrough(tmp_path):
    """BioRunError → error_code 透传（全部基因集空交集时可见）。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR", "ValueError: gene set 'X': no genes found in data")
    out = reg.get("sc_score_genes").handler(
        dataset_ref="d", gene_sets={"X": ["NOPE1"]})
    assert out["error_code"] == "SC_SCRIPT_ERROR"
    assert "no genes" in out["error_message"]


def test_sc_score_genes_schema_limits(tmp_path):
    """gene_sets object schema 限 1..8 个集（防 planner 一次塞爆）。"""
    reg, _ = _registry(tmp_path)
    props = reg.get("sc_score_genes").parameters["properties"]
    assert props["gene_sets"]["minProperties"] == 1
    assert props["gene_sets"]["maxProperties"] == 8
    assert set(reg.get("sc_score_genes").parameters["required"]) == {
        "dataset_ref", "gene_sets"}
    assert reg.get("sc_score_genes").timeout_sec == 1800


def test_sc_metabolism_forwards_params(tmp_path):
    """handler 转发 metabolism 脚本参数（top_n 降维输出数）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "n_pathways_scored": 280,
        "top_pathways": [{"term": "Glycolysis"}]}
    out = reg.get("sc_metabolism").handler(dataset_ref="d", top_n=50)
    args = runner.run.call_args.args
    assert args[0] == "metabolism"
    assert args[1]["top_n"] == 50
    assert "ok" not in out
    assert out["n_pathways_scored"] == 280


def test_sc_pseudotime_forwards_params(tmp_path):
    """handler 转发 pseudotime 脚本参数（root_marker 定根）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "diffmap_dpt",
        "root_note": "NKG7-highest cell #42"}
    out = reg.get("sc_pseudotime").handler(
        dataset_ref="d", root_marker="NKG7")
    args = runner.run.call_args.args
    assert args[0] == "pseudotime"
    assert args[1]["root_marker"] == "NKG7"
    assert "ok" not in out
    assert out["method"] == "diffmap_dpt"
    assert reg.get("sc_pseudotime").timeout_sec == 1200


def test_sc_pseudotime_error_passthrough(tmp_path):
    """BioRunError → error_code 透传（缺 neighbors 时可见）。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR", "ValueError: lacks neighbors graph")
    out = reg.get("sc_pseudotime").handler(dataset_ref="d")
    assert out["error_code"] == "SC_SCRIPT_ERROR"
    assert "neighbors" in out["error_message"]


def test_sc_pseudotime_dyn_and_root_cluster(tmp_path):
    """Phase 53：dyn_top_n/root_cluster 透传（动态基因+簇定根）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "diffmap_dpt",
        "root_mode": "cluster", "n_dyn": 50}
    reg.get("sc_pseudotime").handler(
        dataset_ref="d", root_cluster="3", dyn_top_n=100)
    args = runner.run.call_args.args
    assert args[1]["root_cluster"] == "3"
    assert args[1]["dyn_top_n"] == 100
    props = reg.get("sc_pseudotime").parameters["properties"]
    assert "root_cluster" in props and "dyn_top_n" in props


def test_sc_pseudotime_defaults_phase53(tmp_path):
    """Phase 53 默认值：dyn_top_n=50、root_cluster=''（现状兼容）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {"ok": True}
    reg.get("sc_pseudotime").handler(dataset_ref="d")
    args = runner.run.call_args.args
    assert args[1]["dyn_top_n"] == 50
    assert args[1]["root_cluster"] == ""


def test_sc_pseudotime_palantir_engine(tmp_path):
    """Palantir 引擎：engine/start_cell 透传 + schema 含两参数。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "palantir",
        "root_mode": "explicit", "n_terminal": 2}
    out = reg.get("sc_pseudotime").handler(
        dataset_ref="d", engine="palantir", start_cell="AAAC-1")
    args = runner.run.call_args.args
    assert args[1]["engine"] == "palantir"
    assert args[1]["start_cell"] == "AAAC-1"
    assert out["method"] == "palantir"
    props = reg.get("sc_pseudotime").parameters["properties"]
    assert "engine" in props and "start_cell" in props


def test_sc_pseudotime_defaults_palantir_phase(tmp_path):
    """Palantir 相默认值：engine='dpt'、start_cell=''（回归零变化）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {"ok": True}
    reg.get("sc_pseudotime").handler(dataset_ref="d")
    args = runner.run.call_args.args
    assert args[1]["engine"] == "dpt"
    assert args[1]["start_cell"] == ""


def test_sc_pseudotime_branch_top_n(tmp_path):
    """分支推断：branch_top_n 透传 + schema 含参数。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "palantir",
        "n_unassigned": 3, "branch_counts": {"AAAC-1": 120}}
    out = reg.get("sc_pseudotime").handler(
        dataset_ref="d", engine="palantir", branch_top_n=50)
    args = runner.run.call_args.args
    assert args[1]["branch_top_n"] == 50
    assert out["n_unassigned"] == 3
    props = reg.get("sc_pseudotime").parameters["properties"]
    assert "branch_top_n" in props


def test_sc_pseudotime_defaults_branch_phase(tmp_path):
    """分支推断相默认值：branch_top_n=0（跳过，回归零变化）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {"ok": True}
    reg.get("sc_pseudotime").handler(dataset_ref="d")
    args = runner.run.call_args.args
    assert args[1]["branch_top_n"] == 0


def test_sc_pseudotime_dyn_modules(tmp_path):
    """趋势聚类：dyn_modules_k/modules_enrich 透传 + schema 含参数。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "palantir",
        "n_modules": 6, "module_sizes": {"M1": 30}}
    out = reg.get("sc_pseudotime").handler(
        dataset_ref="d", engine="palantir", dyn_modules_k=6,
        modules_enrich="go_bp")
    args = runner.run.call_args.args
    assert args[1]["dyn_modules_k"] == 6
    assert args[1]["modules_enrich"] == "go_bp"
    assert out["n_modules"] == 6
    props = reg.get("sc_pseudotime").parameters["properties"]
    assert "dyn_modules_k" in props and "modules_enrich" in props


def test_sc_pseudotime_defaults_modules_phase(tmp_path):
    """趋势聚类相默认值：dyn_modules_k=0、modules_enrich=''（跳过）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {"ok": True}
    reg.get("sc_pseudotime").handler(dataset_ref="d")
    args = runner.run.call_args.args
    assert args[1]["dyn_modules_k"] == 0
    assert args[1]["modules_enrich"] == ""


def test_sc_pseudotime_slingshot_engine(tmp_path):
    """Slingshot 引擎：engine='slingshot' 透传 + schema 描述含三引擎。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "slingshot",
        "n_lineages": 2}
    out = reg.get("sc_pseudotime").handler(
        dataset_ref="d", engine="slingshot", root_cluster="0")
    args = runner.run.call_args.args
    assert args[1]["engine"] == "slingshot"
    assert args[1]["root_cluster"] == "0"
    assert out["n_lineages"] == 2
    props = reg.get("sc_pseudotime").parameters["properties"]
    assert "slingshot" in props["engine"]["description"]


def test_sc_pseudotime_slingshot_start_cell_schema(tmp_path):
    """start_cell 校验放宽：描述从 palantir 专属 → 双引擎合法。"""
    reg, _ = _registry(tmp_path)
    props = reg.get("sc_pseudotime").parameters["properties"]
    assert "slingshot" in props["start_cell"]["description"].lower()


# === Phase 33：常用分析第二批（组间差异/亚聚类/整合/组成） ===


def test_sc_phase33_tools_registered_l1(tmp_path):
    """四新工具注册可见且 L1_compute（研究链路可规划）。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    for name in ("sc_de", "sc_subcluster", "sc_integrate", "sc_cellfreq"):
        assert name in names
        assert reg.get(name).risk_level == "L1_compute"


def test_sc_de_forwards_params(tmp_path):
    """handler 转发 de 脚本参数（两组定向对比）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "comparison": "treated_vs_control",
        "up": [{"gene": "GZMB"}], "down": []}
    out = reg.get("sc_de").handler(
        dataset_ref="d", groupby="condition", group_a="treated",
        group_b="control", top_n=30)
    args = runner.run.call_args.args
    assert args[0] == "de"
    assert args[1]["groupby"] == "condition"
    assert args[1]["group_a"] == "treated"
    assert args[1]["group_b"] == "control"
    assert args[1]["top_n"] == 30
    assert "ok" not in out
    assert out["up"][0]["gene"] == "GZMB"


def test_sc_de_error_lists_columns(tmp_path):
    """BioRunError → 透传（列不存在时错误含可用列引导自纠）。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR",
        "ValueError: groupby column 'cond' not in obs; available: leiden(5)")
    out = reg.get("sc_de").handler(
        dataset_ref="d", groupby="cond", group_a="x", group_b="y")
    assert out["error_code"] == "SC_SCRIPT_ERROR"
    assert "available" in out["error_message"]


def test_sc_subcluster_forwards_params(tmp_path):
    """handler 转发 subcluster 参数，返回新 dataset_ref 供下游链。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d_sub0-1", "parent_ref": "d",
        "n_cells": 500, "n_clusters": 3}
    out = reg.get("sc_subcluster").handler(
        dataset_ref="d", clusters=["0", "1"], resolution=0.8)
    args = runner.run.call_args.args
    assert args[0] == "subcluster"
    assert args[1]["clusters"] == ["0", "1"]
    assert args[1]["resolution"] == 0.8
    assert out["dataset_ref"] == "d_sub0-1"
    assert reg.get("sc_subcluster").timeout_sec == 1800


def test_sc_integrate_forwards_params(tmp_path):
    """handler 转发 integrate 参数（batch 列 + bbknn 锁定）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d_bbknn", "n_batches": 4,
        "method": "bbknn"}
    out = reg.get("sc_integrate").handler(dataset_ref="d", batch="sample")
    args = runner.run.call_args.args
    assert args[0] == "integrate"
    assert args[1]["batch"] == "sample"
    assert args[1]["method"] == "bbknn"
    assert out["dataset_ref"] == "d_bbknn"
    props = reg.get("sc_integrate").parameters["properties"]
    assert props["method"]["enum"] == ["bbknn"]


def test_sc_cellfreq_forwards_params(tmp_path):
    """handler 转发 cellfreq 参数（by 必填 + group 可选）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "chi2_tests": [
            {"cluster": "3", "p": 1e-4}]}
    out = reg.get("sc_cellfreq").handler(
        dataset_ref="d", by="sample", group="condition")
    args = runner.run.call_args.args
    assert args[0] == "cellfreq"
    assert args[1]["by"] == "sample"
    assert args[1]["group"] == "condition"
    assert out["chi2_tests"][0]["cluster"] == "3"
    assert reg.get("sc_cellfreq").timeout_sec == 600


# === Phase 34：B 类分析（细胞通讯/差异丰度/bulk 解卷积） ===


def test_sc_phase34_tools_registered_l1(tmp_path):
    """三新工具注册可见且 L1_compute（研究链路可规划）。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    for name in ("sc_cellchat", "sc_milo", "sc_deconv"):
        assert name in names
        assert reg.get(name).risk_level == "L1_compute"


def test_sc_cellchat_forwards_params(tmp_path):
    """handler 转发 cellchat 参数（species→资源库、min_cells）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "resource": "consensus",
        "n_sig": 12, "top": [{"ligand": "CXCL12"}]}
    out = reg.get("sc_cellchat").handler(
        dataset_ref="d", celltype_col="leiden", species="human")
    args = runner.run.call_args.args
    assert args[0] == "cellchat"
    assert args[1]["celltype_col"] == "leiden"
    assert args[1]["species"] == "human"
    assert "ok" not in out
    assert out["top"][0]["ligand"] == "CXCL12"
    assert reg.get("sc_cellchat").timeout_sec == 3600


def test_sc_cellchat_error_lists_columns(tmp_path):
    """BioRunError → 透传（细胞标签列不存在时错误含可用列引导自纠）。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR",
        "ValueError: celltype column 'celltype' not in obs; available: "
        "leiden(5)")
    out = reg.get("sc_cellchat").handler(
        dataset_ref="d", celltype_col="celltype")
    assert out["error_code"] == "SC_SCRIPT_ERROR"
    assert "available" in out["error_message"]


def test_sc_cellchat_method_and_group_col(tmp_path):
    """Phase 47：method=rank_aggregate + group_col 差异通讯透传。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {"ok": True, "dataset_ref": "d",
                               "method": "rank_aggregate", "n_sig": 5}
    reg.get("sc_cellchat").handler(
        dataset_ref="d", celltype_col="celltypist_label",
        method="rank_aggregate", group_col="group")
    args = runner.run.call_args.args
    assert args[1]["method"] == "rank_aggregate"
    assert args[1]["group_col"] == "group"
    props = reg.get("sc_cellchat").parameters["properties"]
    assert props["method"]["enum"] == ["cellchat", "rank_aggregate"]


def test_sc_cellchat_defaults_method_group(tmp_path):
    """Phase 47 默认值：method=cellchat、group_col=''（单组现状不变）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {"ok": True}
    reg.get("sc_cellchat").handler(dataset_ref="d")
    args = runner.run.call_args.args
    assert args[1]["method"] == "cellchat"
    assert args[1]["group_col"] == ""


def test_sc_milo_forwards_params(tmp_path):
    """handler 转发 milo 参数（样本列+分组列+定向对比）。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "comparison": "treated_vs_control",
        "n_nhoods": 40, "n_sig_fdr01": 3, "top": []}
    out = reg.get("sc_milo").handler(
        dataset_ref="d", sample_col="sample", group_col="condition",
        group_a="treated", group_b="control")
    args = runner.run.call_args.args
    assert args[0] == "milo"
    assert args[1]["sample_col"] == "sample"
    assert args[1]["group_a"] == "treated"
    assert out["n_sig_fdr01"] == 3
    assert reg.get("sc_milo").timeout_sec == 3600


def test_sc_milo_error_passthrough(tmp_path):
    """样本数不足等脚本错误透传。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR",
        "ValueError: need >=2 samples per group: treated=1, control=3")
    out = reg.get("sc_milo").handler(
        dataset_ref="d", sample_col="sample", group_col="condition",
        group_a="treated", group_b="control")
    assert out["error_code"] == "SC_SCRIPT_ERROR"
    assert ">=2 samples" in out["error_message"]


def test_sc_deconv_forwards_params(tmp_path):
    """bulk_file 走 resolve_data_path 白名单 + /data 挂载转发。"""
    from pathlib import Path

    reg, runner = _registry(tmp_path)
    runner.resolve_data_path.return_value = (
        Path("D:/sc_data"), "bulk.csv", "D:/sc_data/bulk.csv")
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "wnnls",
        "n_samples": 3, "dominant": []}
    out = reg.get("sc_deconv").handler(
        dataset_ref="d", bulk_file="D:/sc_data/bulk.csv")
    assert runner.resolve_data_path.call_args.args[0] == "D:/sc_data/bulk.csv"
    call = runner.run.call_args
    assert call.args[0] == "deconv"
    assert call.args[1]["bulk_path"] == "bulk.csv"
    assert call.kwargs["mounts"] == [(Path("D:/sc_data"), "/data")]
    assert "ok" not in out


def test_sc_deconv_schema_enum(tmp_path):
    """method enum 锁定 wnnls/nusvr。"""
    reg, _ = _registry(tmp_path)
    props = reg.get("sc_deconv").parameters["properties"]
    assert props["method"]["enum"] == ["wnnls", "nusvr"]
    assert reg.get("sc_deconv").parameters["required"] == [
        "dataset_ref", "bulk_file"]


# === Phase 35：注释与质控补强（annotate/meta/doublet/cellcycle） ===


def test_sc_phase35_tools_registered_l1(tmp_path):
    """四新工具注册可见且 L1_compute。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    for name in ("sc_annotate", "sc_meta", "sc_doublet", "sc_cellcycle"):
        assert name in names
        assert reg.get(name).risk_level == "L1_compute"


def test_sc_annotate_celltypist_forwards(tmp_path):
    """celltypist 路转发 model 与 celltype_col。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "celltypist",
        "label_col": "celltypist_label", "n_labels": 5}
    out = reg.get("sc_annotate").handler(
        dataset_ref="d", method="celltypist", model="Immune_All_High.pkl")
    args = runner.run.call_args.args
    assert args[0] == "annotate"
    assert args[1]["method"] == "celltypist"
    assert args[1]["model"] == "Immune_All_High.pkl"
    assert "ok" not in out
    assert reg.get("sc_annotate").timeout_sec == 1800


def test_sc_annotate_markers_forwards(tmp_path):
    """markers 路转发 marker_sets 字典。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "method": "markers",
        "label_col": "annotation",
        "cluster_assignment": {"0": "T cell"}}
    out = reg.get("sc_annotate").handler(
        dataset_ref="d", method="markers",
        marker_sets={"T cell": ["CD3D", "CD3E"]})
    args = runner.run.call_args.args
    assert args[1]["marker_sets"] == {"T cell": ["CD3D", "CD3E"]}
    assert out["cluster_assignment"]["0"] == "T cell"


def test_sc_annotate_schema_enum(tmp_path):
    """method enum 锁定 celltypist/markers。"""
    reg, _ = _registry(tmp_path)
    props = reg.get("sc_annotate").parameters["properties"]
    assert props["method"]["enum"] == ["celltypist", "markers"]


def test_sc_meta_merge_csv_mounts(tmp_path):
    """merge_csv 走 resolve_data_path 白名单 + /data 挂载转发。"""
    from pathlib import Path

    reg, runner = _registry(tmp_path)
    runner.resolve_data_path.return_value = (
        Path("D:/sc_data"), "meta.csv", "D:/sc_data/meta.csv")
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "op": "merge_csv",
        "changed_cols": ["condition"]}
    out = reg.get("sc_meta").handler(
        dataset_ref="d", op="merge_csv", csv_file="D:/sc_data/meta.csv",
        key_col="sample")
    call = runner.run.call_args
    assert call.args[0] == "meta"
    assert call.args[1]["csv_path"] == "meta.csv"
    assert call.args[1]["key_col"] == "sample"
    assert call.kwargs["mounts"] == [(Path("D:/sc_data"), "/data")]
    assert "ok" not in out


def test_sc_meta_map_values_forwards(tmp_path):
    """map_values 不挂 /data，mapping 透传。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "op": "map_values",
        "changed_cols": ["celltype"]}
    out = reg.get("sc_meta").handler(
        dataset_ref="d", op="map_values", col="leiden",
        mapping={"0": "T"}, out_col="celltype")
    call = runner.run.call_args
    assert call.args[1]["mapping"] == {"0": "T"}
    assert call.args[1]["out_col"] == "celltype"
    assert call.kwargs.get("mounts") is None
    runner.resolve_data_path.assert_not_called()


def test_sc_meta_error_passthrough(tmp_path):
    """脚本错误透传（列不存在等）。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR", "ValueError: column 'foo' not in obs")
    out = reg.get("sc_meta").handler(dataset_ref="d", op="rename_col",
                                     old="foo", new="bar")
    assert out["error_code"] == "SC_SCRIPT_ERROR"


def test_sc_doublet_forwards_params(tmp_path):
    """doublet 转发 expected_rate；timeout 1200。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "n_doublets": 12,
        "doublet_rate": 0.05}
    out = reg.get("sc_doublet").handler(dataset_ref="d", expected_rate=0.08)
    args = runner.run.call_args.args
    assert args[0] == "doublet"
    assert args[1]["expected_rate"] == 0.08
    assert out["n_doublets"] == 12
    assert reg.get("sc_doublet").timeout_sec == 1200


def test_sc_cellcycle_forwards_params(tmp_path):
    """cellcycle 转发；timeout 600。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d",
        "phase_counts": {"G1": 150, "S": 30, "G2M": 20}}
    out = reg.get("sc_cellcycle").handler(dataset_ref="d")
    args = runner.run.call_args.args
    assert args[0] == "cellcycle"
    assert out["phase_counts"]["G1"] == 150
    assert reg.get("sc_cellcycle").timeout_sec == 600


# === Phase 36：调控网络（scenic） ===


def test_sc_scenic_registered_l1(tmp_path):
    """sc_scenic 注册可见、L1_compute、timeout 3600。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    assert "sc_scenic" in names
    spec = reg.get("sc_scenic")
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 3600


def test_sc_scenic_forwards_and_mounts(tmp_path):
    """db 目录存在时转发参数并只读挂载两个 DB 目录。"""
    from orchestrator.tools.builtin.l3_singlecell import (
        register_l3_singlecell,
    )
    (tmp_path / "cisTarget_databases").mkdir()
    (tmp_path / "motifAnnotations").mkdir()
    reg = ToolRegistry()
    runner = SimpleNamespace(
        run=MagicMock(), resolve_data_path=MagicMock())
    register_l3_singlecell(reg, runner, bio_scenic_db_root=str(tmp_path))
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "species": "human",
        "n_regulons": 12, "top_regulons_by_cluster": {"0": ["SPI1(+)"]}}
    out = reg.get("sc_scenic").handler(dataset_ref="d", species="human",
                                       db="10kb", max_cells=1500)
    call = runner.run.call_args
    assert call.args[0] == "scenic"
    assert call.args[1]["db"] == "10kb"
    assert call.args[1]["max_cells"] == 1500
    mount_targets = sorted(m[1] for m in call.kwargs["mounts"])
    assert mount_targets == ["/scenic_db/cistarget", "/scenic_db/motifannot"]
    assert call.kwargs["timeout_sec"] == 3600
    assert "ok" not in out
    assert out["n_regulons"] == 12


def test_sc_scenic_config_error_when_db_missing(tmp_path):
    """DB 目录缺失时不进容器，直接 SC_CONFIG 错误。"""
    from orchestrator.tools.builtin.l3_singlecell import (
        register_l3_singlecell,
    )
    reg = ToolRegistry()
    runner = SimpleNamespace(
        run=MagicMock(), resolve_data_path=MagicMock())
    register_l3_singlecell(reg, runner, bio_scenic_db_root=str(tmp_path))
    out = reg.get("sc_scenic").handler(dataset_ref="d")
    assert out["error_code"] == "SC_CONFIG"
    runner.run.assert_not_called()


def test_sc_scenic_schema_enums(tmp_path):
    """species/db enum 锁定。"""
    reg, _ = _registry(tmp_path)
    props = reg.get("sc_scenic").parameters["properties"]
    assert props["species"]["enum"] == ["human", "mouse"]
    assert props["db"]["enum"] == ["500bp", "10kb", "both"]


# === Phase 37：WNN 多组学 + 虚拟敲除 ===


def test_sc_phase37_tools_registered_l1(tmp_path):
    """两新工具注册可见、L1_compute、超时正确。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    for name in ("sc_wnn", "sc_knockout"):
        assert name in names
        assert reg.get(name).risk_level == "L1_compute"
    assert reg.get("sc_wnn").timeout_sec == 1200
    assert reg.get("sc_knockout").timeout_sec == 3600


def test_sc_wnn_same_root_single_mount(tmp_path):
    """两文件同数据根 → 单 /data 挂载；新 dataset_id 为双 hash 拼接。"""
    from pathlib import Path

    reg, runner = _registry(tmp_path)
    root = Path("D:/sc_data")
    runner.resolve_data_path.side_effect = [
        (root, "rna.h5ad", "D:/sc_data/rna.h5ad"),
        (root, "adt.h5ad", "D:/sc_data/adt.h5ad"),
    ]
    runner.run.return_value = {"ok": True, "dataset_ref": "abc123def456",
                               "n_cells": 900, "n_clusters": 8}
    with patch("orchestrator.tools.builtin.l3_singlecell.compute_dataset_id", side_effect=["abcdef123456", "789012fedcba"]):
        out = reg.get("sc_wnn").handler(rna_file="D:/sc_data/rna.h5ad",
                                        adt_file="D:/sc_data/adt.h5ad")
    call = runner.run.call_args
    assert call.args[0] == "wnn"
    assert len(call.kwargs["mounts"]) == 1
    assert call.kwargs["mounts"][0] == (root, "/data")
    assert len(call.args[1]["dataset_id"]) == 12
    assert call.kwargs["timeout_sec"] == 1200
    assert out["n_clusters"] == 8 and "ok" not in out


def test_sc_wnn_cross_root_dual_mount(tmp_path):
    """两文件异数据根 → 双挂载 + adt_path 容器绝对路径。"""
    from pathlib import Path

    reg, runner = _registry(tmp_path)
    runner.resolve_data_path.side_effect = [
        (Path("D:/sc_data"), "rna.h5ad", "D:/sc_data/rna.h5ad"),
        (Path("E:/adt"), "adt.h5ad", "E:/adt/adt.h5ad"),
    ]
    runner.run.return_value = {"ok": True, "dataset_ref": "x" * 12}
    with patch("orchestrator.tools.builtin.l3_singlecell.compute_dataset_id", side_effect=["abcdef123456", "789012fedcba"]):
        reg.get("sc_wnn").handler(rna_file="D:/sc_data/rna.h5ad",
                                  adt_file="E:/adt/adt.h5ad")
    call = runner.run.call_args
    targets = sorted(m[1] for m in call.kwargs["mounts"])
    assert targets == ["/data", "/data_adt"]
    assert call.args[1]["adt_path"].startswith("/data_adt/")


def test_sc_wnn_schema_required(tmp_path):
    """required 锁定双文件参数。"""
    reg, _ = _registry(tmp_path)
    params = reg.get("sc_wnn").parameters
    assert params["required"] == ["rna_file", "adt_file"]


def test_sc_knockout_forwards_params(tmp_path):
    """knockout 转发 gko 与网络参数。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "gko": "SPI1",
        "top_dr_genes": ["CTSS", "LYZ"], "n_genes": 1000}
    out = reg.get("sc_knockout").handler(
        dataset_ref="d", gko="SPI1", celltype_col="leiden", group="2",
        n_genes=800, n_net=5)
    args = runner.run.call_args
    assert args.args[0] == "knockout"
    assert args.args[1]["gko"] == "SPI1"
    assert args.args[1]["n_net"] == 5
    assert args.args[1]["group"] == "2"
    assert args.kwargs["timeout_sec"] == 3600
    assert out["top_dr_genes"] == ["CTSS", "LYZ"] and "ok" not in out


def test_sc_knockout_error_passthrough(tmp_path):
    """R 侧失败透传错误码。"""
    reg, runner = _registry(tmp_path)
    runner.run.side_effect = BioRunError(
        "SC_SCRIPT_ERROR", "RuntimeError: Rscript knk.R failed")
    out = reg.get("sc_knockout").handler(dataset_ref="d", gko="XX")
    assert out["error_code"] == "SC_SCRIPT_ERROR"


# === 统一测试轮前置：物种适配 ===


def test_sc_metabolism_forwards_species_mouse(tmp_path):
    """handler 转发 species=mouse 到 payload；schema enum 锁定 human/mouse。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "species": "mouse",
        "n_pathways_scored": 200, "top_pathways": []}
    out = reg.get("sc_metabolism").handler(
        dataset_ref="d", top_n=20, species="mouse")
    args = runner.run.call_args.args
    assert args[0] == "metabolism"
    assert args[1]["species"] == "mouse"
    assert args[1]["top_n"] == 20
    assert "ok" not in out
    assert out["species"] == "mouse"
    props = reg.get("sc_metabolism").parameters["properties"]
    assert props["species"]["enum"] == ["human", "mouse"]
    assert props["species"]["default"] == "human"


def test_sc_cellchat_forwards_species_mouse(tmp_path):
    """sc_cellchat 已支持 species：断言 handler 转发 mouse 现状。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "resource": "consensus",
        "species": "mouse", "n_sig": 8, "top": []}
    out = reg.get("sc_cellchat").handler(
        dataset_ref="d", species="mouse")
    args = runner.run.call_args.args
    assert args[0] == "cellchat"
    assert args[1]["species"] == "mouse"
    assert "ok" not in out
    assert out["species"] == "mouse"
    props = reg.get("sc_cellchat").parameters["properties"]
    assert props["species"]["enum"] == ["human", "mouse"]


# === 耗时工具抽样上限（默认 0=全量，显式抽样加速） ===


def test_sc_cellchat_forwards_max_cells_per_group(tmp_path):
    """handler 转发 max_cells_per_group=100 到 payload；schema 默认 0。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "resource": "consensus",
        "n_cells_used": 800, "subsampled": True, "n_sig": 12, "top": []}
    out = reg.get("sc_cellchat").handler(
        dataset_ref="d", species="mouse", max_cells_per_group=100)
    args = runner.run.call_args.args
    assert args[0] == "cellchat"
    assert args[1]["max_cells_per_group"] == 100
    assert "ok" not in out
    assert out["subsampled"] is True
    props = reg.get("sc_cellchat").parameters["properties"]
    assert props["max_cells_per_group"]["default"] == 0


def test_sc_milo_forwards_max_cells_per_sample(tmp_path):
    """handler 转发 max_cells_per_sample=100 到 payload；schema 默认 0。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "comparison": "A_vs_B",
        "n_cells_used": 600, "subsampled": True, "n_nhoods": 40,
        "n_sig_fdr01": 3, "top": []}
    out = reg.get("sc_milo").handler(
        dataset_ref="d", sample_col="sample", group_col="condition",
        group_a="A", group_b="B", max_cells_per_sample=100)
    args = runner.run.call_args.args
    assert args[0] == "milo"
    assert args[1]["max_cells_per_sample"] == 100
    assert "ok" not in out
    assert out["subsampled"] is True
    props = reg.get("sc_milo").parameters["properties"]
    assert props["max_cells_per_sample"]["default"] == 0


# === B1：sc_cnv 注册与参数透传 ===

def test_register_sc_cnv(tmp_path):
    """B1 CNV：sc_cnv 注册、planner 可见、L1_compute。"""
    reg, _ = _registry(tmp_path)
    names = [t.name for t in reg.list(planner_visible=True)]
    assert "sc_cnv" in names
    assert reg.get("sc_cnv").risk_level == "L1_compute"
    assert reg.get("sc_cnv").timeout_sec == 3600


def test_sc_cnv_forwards_params(tmp_path):
    """B1 CNV：参数透传进容器 payload，timeout 与 ToolSpec 一致。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {
        "ok": True, "dataset_ref": "d", "n_malignant": 5}

    out = reg.get("sc_cnv").handler(
        dataset_ref="d", method="cnvturbo",
        celltype_col="celltypist_label",
        ref_groups=["T cells"], resolution=0.8, cluster_smooth=True)

    script, payload = runner.run.call_args[0]
    assert script == "cnv"
    assert payload == {"dataset_id": "d", "method": "cnvturbo",
                       "celltype_col": "celltypist_label",
                       "ref_groups": ["T cells"], "resolution": 0.8,
                       "cluster_smooth": True}
    assert runner.run.call_args[1]["timeout_sec"] == 3600
    assert "ok" not in out
    assert out["n_malignant"] == 5


def test_sc_cnv_default_params(tmp_path):
    """B1 CNV：缺省 method=infercnvpy / celltype_col=leiden / ref_groups=None。"""
    reg, runner = _registry(tmp_path)
    runner.run.return_value = {"ok": True, "dataset_ref": "d"}

    reg.get("sc_cnv").handler(dataset_ref="d")

    _, payload = runner.run.call_args[0]
    assert payload == {"dataset_id": "d", "method": "infercnvpy",
                       "celltype_col": "leiden", "ref_groups": None,
                       "resolution": 1.0, "cluster_smooth": False}
