"""st_integrate 注册测试（mock BioRunner，不发 docker）。"""
from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_spatial import register_l3_spatial
from orchestrator.tools.tool_registry import ToolRegistry

BIO_IMAGE = "feishu-research-agent/bio:cpu-latest"
REFS = ["sliceA", "sliceB"]


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {"ok": True,
                          "dataset_ref": "sliceA__sliceB_harmony",
                          "n_slices": 2, "n_cells": 800}
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_integrate_registered(reg):
    """st_* 第 18 工具：L1_compute、timeout 1800、dataset_refs 必填。"""
    spec = reg.get("st_integrate")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 1800
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_refs"]
    assert props["dataset_refs"]["type"] == "array"
    assert props["dataset_refs"]["minItems"] == 2
    assert props["method"]["default"] == "harmony"
    assert props["method"]["enum"] == ["harmony", "bbknn"]


def test_st_integrate_dispatches_bio_image(runner, reg):
    """Phase 69：跨镜像分发——bio 镜像 + /opt/sc_tools（harmonypy 单点
    安装），list 参数原样透传 + timeout 1800。"""
    out = reg.get("st_integrate").handler(dataset_refs=REFS)
    args, kw = runner.run.call_args
    assert args[0] == "st_integrate"
    assert args[1]["dataset_refs"] == REFS
    assert kw["image"] == BIO_IMAGE
    assert kw["script_dir"] == "/opt/sc_tools"
    assert kw["timeout_sec"] == 1800
    assert out["dataset_ref"] == "sliceA__sliceB_harmony"
    assert "ok" not in out


def test_st_integrate_defaults(runner, reg):
    """默认值：method/n_top_hvg/n_pcs/n_neighbors/resolution/slice_col/
    spatial_offset。"""
    reg.get("st_integrate").handler(dataset_refs=REFS)
    args, _ = runner.run.call_args
    assert args[1]["method"] == "harmony"
    assert args[1]["n_top_hvg"] == 2000
    assert args[1]["n_pcs"] == 30
    assert args[1]["n_neighbors"] == 15
    assert args[1]["resolution"] == 1.0
    assert args[1]["slice_col"] == "slice"
    assert args[1]["spatial_offset"] is False


def test_st_integrate_list_passthrough(runner, reg):
    """>2 片 list 透传不变形（首个 list 参数工具，契约钉死）。"""
    many = ["a", "b", "c", "d"]
    reg.get("st_integrate").handler(dataset_refs=many)
    args, _ = runner.run.call_args
    assert args[1]["dataset_refs"] == many
    assert isinstance(args[1]["dataset_refs"], list)


def test_st_integrate_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "ST_INTEG_REF_MISSING", "dataset 'x' not found")
    out = reg.get("st_integrate").handler(dataset_refs=REFS)
    assert out == {"error_code": "ST_INTEG_REF_MISSING",
                   "error_message": "dataset 'x' not found"}
