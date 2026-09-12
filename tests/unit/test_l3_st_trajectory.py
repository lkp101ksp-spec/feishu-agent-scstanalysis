"""st_trajectory 注册测试（mock BioRunner，不发 docker）。"""
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
                          "n_spots": 144, "method": "diffmap_dpt"}
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_trajectory_registered(reg):
    """st_* 第 14 工具注册为 L1_compute，timeout 600，root_mode 默认 marker。"""
    spec = reg.get("st_trajectory")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 600
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["root_mode"]["default"] == "marker"
    assert props["root_marker"]["default"] == ""
    assert props["root_layer"]["default"] == "tumor"


def test_st_trajectory_forwards_params(runner, reg):
    """全参数透传 + st 镜像 + /opt/st_tools + timeout 600。"""
    reg.get("st_trajectory").handler(
        dataset_ref="abc123", root_mode="vicinity",
        root_marker="MKI67", root_layer="distal")
    args, kw = runner.run.call_args
    assert args[0] == "trajectory"
    assert args[1] == {"dataset_id": "abc123",
                       "root_mode": "vicinity",
                       "root_marker": "MKI67",
                       "root_layer": "distal"}
    assert kw["image"] == ST_IMAGE
    assert kw["script_dir"] == "/opt/st_tools"
    assert kw["timeout_sec"] == 600


def test_st_trajectory_defaults(runner, reg):
    """缺省：root_mode=marker / root_marker="" / root_layer=tumor。"""
    reg.get("st_trajectory").handler(dataset_ref="abc123")
    args, _ = runner.run.call_args
    assert args[1]["root_mode"] == "marker"
    assert args[1]["root_marker"] == ""
    assert args[1]["root_layer"] == "tumor"


def test_st_trajectory_ok_stripped(runner, reg):
    """emit 的 ok 键被 handler 摘除（l3 全家同口径）。"""
    out = reg.get("st_trajectory").handler(dataset_ref="abc123")
    assert "ok" not in out
    assert out["n_spots"] == 144


def test_st_trajectory_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "ST_TRAJ_NO_VICINITY", "obs['vicinity'] 不存在；先跑 st_vicinity")
    out = reg.get("st_trajectory").handler(
        dataset_ref="abc123", root_mode="vicinity")
    assert out == {"error_code": "ST_TRAJ_NO_VICINITY",
                   "error_message": "obs['vicinity'] 不存在；先跑 st_vicinity"}
