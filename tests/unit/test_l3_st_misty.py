"""st_misty 注册测试（mock BioRunner，不发 docker）。"""
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
                          "n_targets": 8, "n_predictors": 50,
                          "mean_gain_R2": 0.12}
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_misty_registered(reg):
    """st_* 第 13 工具注册为 L1_compute，timeout 1800，n_hvg 默认 50。"""
    spec = reg.get("st_misty")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 1800
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["n_hvg"]["default"] == 50
    assert "bandwidth" in props
    assert props["extra_mode"]["default"] == "hvg"


def test_st_misty_forwards_params(runner, reg):
    """全参数透传 + st 镜像 + /opt/st_tools + timeout 1800。"""
    reg.get("st_misty").handler(
        dataset_ref="abc123", n_hvg=80, bandwidth=250.0)
    args, kw = runner.run.call_args
    assert args[0] == "misty"
    assert args[1] == {"dataset_id": "abc123", "n_hvg": 80,
                       "bandwidth": 250.0, "extra_mode": "hvg"}
    assert kw["image"] == ST_IMAGE
    assert kw["script_dir"] == "/opt/st_tools"
    assert kw["timeout_sec"] == 1800


def test_st_misty_defaults(runner, reg):
    """默认值：n_hvg=50 / bandwidth=0（auto）。"""
    reg.get("st_misty").handler(dataset_ref="abc123")
    args, _ = runner.run.call_args
    assert args[1]["n_hvg"] == 50
    assert args[1]["bandwidth"] == 0


def test_st_misty_ok_stripped(runner, reg):
    """emit 的 ok 键被 handler 摘除（l3 全家同口径）。"""
    out = reg.get("st_misty").handler(dataset_ref="abc123")
    assert "ok" not in out
    assert out["n_targets"] == 8


def test_st_misty_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "ST_MISTY_NO_DECONV", "deconv.h5ad 不存在")
    out = reg.get("st_misty").handler(dataset_ref="abc123")
    assert out == {"error_code": "ST_MISTY_NO_DECONV",
                   "error_message": "deconv.h5ad 不存在"}


def test_st_misty_extra_mode_default(runner, reg):
    """extra_mode 默认 hvg（向后兼容）并透传进 args payload。"""
    reg.get("st_misty").handler(dataset_ref="abc123")
    args, _ = runner.run.call_args
    assert args[1]["extra_mode"] == "hvg"


def test_st_misty_extra_mode_forwarded(runner, reg):
    """extra_mode=progeny 透传进 args payload。"""
    reg.get("st_misty").handler(dataset_ref="abc123",
                                extra_mode="progeny")
    args, _ = runner.run.call_args
    assert args[1]["extra_mode"] == "progeny"
