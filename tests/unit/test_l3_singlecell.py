"""Phase 20：sc_* 5 工具注册单测（fake BioRunner，不起容器）。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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
