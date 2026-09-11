"""st_cnv 注册测试（mock BioRunner，不发 docker）。"""
from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_spatial import register_l3_spatial
from orchestrator.tools.tool_registry import ToolRegistry

ST_IMAGE = "feishu-research-agent/bio:st-cpu-latest"


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {"ok": True, "dataset_ref": "abc123",
                          "method": "infercnvpy", "n_malignant": 42,
                          "malignant_ratio": 0.35}
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_cnv_registered(reg):
    """st_* 第 10 工具注册为 L1_compute，timeout 3600（对齐 sc_cnv）。"""
    spec = reg.get("st_cnv")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 3600
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert "annotation_key" in props
    assert "ref_groups" in props
    assert "resolution" in props


def test_st_cnv_forwards_params(runner, reg):
    """全参数透传 + st 镜像 + /opt/st_tools + timeout 3600。"""
    reg.get("st_cnv").handler(
        dataset_ref="abc123", annotation_key="deconv",
        ref_groups=["T cells", "Fibroblast"], resolution=0.8)
    args, kw = runner.run.call_args
    assert args[0] == "cnv"
    assert args[1] == {"dataset_id": "abc123",
                       "annotation_key": "deconv",
                       "ref_groups": ["T cells", "Fibroblast"],
                       "resolution": 0.8}
    assert kw["image"] == ST_IMAGE
    assert kw["script_dir"] == "/opt/st_tools"
    assert kw["timeout_sec"] == 3600


def test_st_cnv_defaults(runner, reg):
    """默认值：annotation_key '' / ref_groups None / resolution 1.0。"""
    reg.get("st_cnv").handler(dataset_ref="abc123")
    args, _ = runner.run.call_args
    assert args[1]["annotation_key"] == ""
    assert args[1]["ref_groups"] is None
    assert args[1]["resolution"] == 1.0


def test_st_cnv_ok_stripped(runner, reg):
    """emit 的 ok 键被 handler 摘除（l3 全家同口径）。"""
    out = reg.get("st_cnv").handler(dataset_ref="abc123")
    assert "ok" not in out
    assert out["n_malignant"] == 42


def test_st_cnv_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "ST_CNV_NO_REFERENCE", "no annotation value matches")
    out = reg.get("st_cnv").handler(dataset_ref="abc123")
    assert out == {"error_code": "ST_CNV_NO_REFERENCE",
                   "error_message": "no annotation value matches"}
