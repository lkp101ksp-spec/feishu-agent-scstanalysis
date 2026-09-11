"""st_niche 注册测试（mock BioRunner，不发 docker）。"""
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
                          "k": 12, "n_niches": 12,
                          "niche_sizes": {"N1": 120}}
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_niche_registered(reg):
    """st_* 第 11 工具注册为 L1_compute，timeout 600，k 默认 12。"""
    spec = reg.get("st_niche")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 600
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["k"]["default"] == 12


def test_st_niche_forwards_params(runner, reg):
    """全参数透传 + st 镜像 + /opt/st_tools + timeout 600。"""
    reg.get("st_niche").handler(dataset_ref="abc123", k=8)
    args, kw = runner.run.call_args
    assert args[0] == "niche"
    assert args[1] == {"dataset_id": "abc123", "k": 8}
    assert kw["image"] == ST_IMAGE
    assert kw["script_dir"] == "/opt/st_tools"
    assert kw["timeout_sec"] == 600


def test_st_niche_defaults(runner, reg):
    """默认值：k=12。"""
    reg.get("st_niche").handler(dataset_ref="abc123")
    args, _ = runner.run.call_args
    assert args[1]["k"] == 12


def test_st_niche_ok_stripped(runner, reg):
    """emit 的 ok 键被 handler 摘除（l3 全家同口径）。"""
    out = reg.get("st_niche").handler(dataset_ref="abc123")
    assert "ok" not in out
    assert out["n_niches"] == 12


def test_st_niche_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "ST_NICHE_NO_DECONV", "deconv.h5ad 不存在")
    out = reg.get("st_niche").handler(dataset_ref="abc123")
    assert out == {"error_code": "ST_NICHE_NO_DECONV",
                   "error_message": "deconv.h5ad 不存在"}
