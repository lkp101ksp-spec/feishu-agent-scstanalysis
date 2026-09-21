"""st_score_weight 注册测试（mock BioRunner，不发 docker）。"""

from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_spatial import (
    _ST_SCORE_WEIGHT_TIMEOUT,
    register_l3_spatial,
)
from orchestrator.tools.tool_registry import ToolRegistry


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {
        "ok": True,
        "dataset_ref": "oscc",
        "source": "st_genescore",
        "n_celltypes": 13,
        "n_pathways": 14,
        "n_spots_overlap": 1749,
        "dropped_celltypes": [],
        "top_by_celltype": {"Epithelial cells": ["JAK-STAT", "EGFR", "TGFb"]},
        "products": {
            "scores_csv": "/ws/oscc/st_score_weight/st_weighted_st_genescore_scores.csv",
            "heatmap_png": "/ws/oscc/st_score_weight/st_weighted_st_genescore_heatmap.png",
        },
    }
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_score_weight_registered(reg):
    """第 21 个 st_* 工具：L1_compute、timeout 600（附录 A 档）、
    浅层 schema、required 仅 dataset_ref、source 默认 st_genescore。"""
    spec = reg.get("st_score_weight")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 600 == _ST_SCORE_WEIGHT_TIMEOUT
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["source"]["default"] == "st_genescore"
    assert props["source"]["enum"] == ["st_genescore", "st_metabolism"]
    for p in props.values():
        assert p["type"] in ("string", "integer", "number", "boolean")


def test_st_score_weight_dispatches_bio_image(runner, reg):
    """跨镜像分发（bio 镜像 + /opt/sc_tools，st_genescore 先例）+
    两参数透传（dataset_ref→dataset_id 容器侧键名）。"""
    out = reg.get("st_score_weight").handler(
        dataset_ref="oscc", source="st_metabolism")
    args, kw = runner.run.call_args
    assert args[0] == "st_score_weight"
    assert args[1] == {"dataset_id": "oscc", "source": "st_metabolism"}
    assert kw["image"] == "feishu-research-agent/bio:cpu-latest"
    assert kw["script_dir"] == "/opt/sc_tools"
    assert kw["timeout_sec"] == _ST_SCORE_WEIGHT_TIMEOUT
    assert out["n_celltypes"] == 13
    assert "ok" not in out


def test_st_score_weight_defaults(runner, reg):
    """可选参数默认：source=st_genescore。"""
    reg.get("st_score_weight").handler(dataset_ref="d1")
    args, _ = runner.run.call_args
    assert args[1]["source"] == "st_genescore"


def test_st_score_weight_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出（ST_WEIGHT_NO_DECONV 等容器侧码）。"""
    runner.run.side_effect = BioRunError(
        "ST_WEIGHT_NO_DECONV", "deconv.h5ad 缺失：请先运行 st_deconvolve")
    out = reg.get("st_score_weight").handler(dataset_ref="d1")
    assert out == {"error_code": "ST_WEIGHT_NO_DECONV",
                   "error_message": "deconv.h5ad 缺失：请先运行 st_deconvolve"}
