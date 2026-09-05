"""Phase 20：sc_* 5 工具注册单测（fake BioRunner，不起容器）。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

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
    """基因集参数 enum 锁定三个别名，防 planner 幻觉库名。"""
    reg, _ = _registry(tmp_path)
    props = reg.get("sc_enrichment").parameters["properties"]
    assert set(props["gene_sets"]["items"]["enum"]) == {
        "hallmark", "go_bp", "kegg"}
    assert reg.get("sc_enrichment").timeout_sec == 1800
