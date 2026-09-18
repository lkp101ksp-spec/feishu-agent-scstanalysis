"""st_nichenet 注册测试（mock BioRunner，不发 docker）。"""
from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_spatial import register_l3_spatial
from orchestrator.tools.tool_registry import ToolRegistry

BIO_IMAGE = "feishu-research-agent/bio:cpu-latest"

GENESET = ["A2M", "FN1", "COL1A1", "MMP9", "ITGA5"]


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {"ok": True, "dataset_ref": "abc123",
                          "top_ligands": ["A2M"], "n_sender": 40,
                          "n_receiver": 120}
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_nichenet_registered(reg):
    """st_* 第 16 工具注册为 L1_compute，timeout 1800（探针实测档）。"""
    spec = reg.get("st_nichenet")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 1800
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref", "geneset",
                                           "receiver_niche"]
    assert props["groupby"]["default"] == "spatial_domain"
    assert props["max_rings"]["default"] == 1
    assert props["geneset"]["minItems"] == 5


def test_st_nichenet_dispatches_bio_image(runner, reg):
    """Phase 65：跨镜像分发——bio 镜像 + /opt/sc_tools（nichenetr R 桥
    单点安装），参数透传 + timeout 1800。"""
    reg.get("st_nichenet").handler(
        dataset_ref="abc123", geneset=GENESET,
        receiver_niche="N1", species="human")
    args, kw = runner.run.call_args
    assert args[0] == "st_nichenet"
    assert args[1]["receiver_niche"] == "N1"
    assert args[1]["geneset"] == GENESET
    assert kw["image"] == BIO_IMAGE
    assert kw["script_dir"] == "/opt/sc_tools"
    assert kw["timeout_sec"] == 1800


def test_st_nichenet_defaults(runner, reg):
    """默认值：groupby/max_rings/top_n_ligands/min_expr/knn。"""
    reg.get("st_nichenet").handler(
        dataset_ref="abc123", geneset=GENESET,
        receiver_niche="N1", species="human")
    args, _ = runner.run.call_args
    assert args[1]["groupby"] == "spatial_domain"
    assert args[1]["max_rings"] == 1
    assert args[1]["top_n_ligands"] == 30
    assert args[1]["min_expr"] == 0.1
    assert args[1]["knn"] == 6


def test_st_nichenet_ok_stripped(runner, reg):
    """emit 的 ok 键被 handler 摘除（l3 全家同口径）。"""
    out = reg.get("st_nichenet").handler(
        dataset_ref="abc123", geneset=GENESET,
        receiver_niche="N1", species="human")
    assert "ok" not in out
    assert out["top_ligands"] == ["A2M"]


def test_st_nichenet_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "ST_NICHENET_NO_GROUP", "receiver_niche 取值不存在")
    out = reg.get("st_nichenet").handler(
        dataset_ref="abc123", geneset=GENESET,
        receiver_niche="N1", species="human")
    assert out == {"error_code": "ST_NICHENET_NO_GROUP",
                   "error_message": "receiver_niche 取值不存在"}
