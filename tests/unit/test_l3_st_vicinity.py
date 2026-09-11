"""st_vicinity 注册测试（mock BioRunner，不发 docker）。"""
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
                          "n_tumor": 72, "max_layers": 5,
                          "layer_sizes": {"tumor": 72, "L1": 12}}
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_vicinity_registered(reg):
    """st_* 第 12 工具注册为 L1_compute，timeout 600，max_layers 默认 5。"""
    spec = reg.get("st_vicinity")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 600
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["max_layers"]["default"] == 5
    assert "coord_type" in props


def test_st_vicinity_forwards_params(runner, reg):
    """全参数透传 + st 镜像 + /opt/st_tools + timeout 600。"""
    reg.get("st_vicinity").handler(
        dataset_ref="abc123", max_layers=3, coord_type="generic")
    args, kw = runner.run.call_args
    assert args[0] == "vicinity"
    assert args[1] == {"dataset_id": "abc123", "max_layers": 3,
                       "coord_type": "generic"}
    assert kw["image"] == ST_IMAGE
    assert kw["script_dir"] == "/opt/st_tools"
    assert kw["timeout_sec"] == 600


def test_st_vicinity_defaults(runner, reg):
    """默认值：max_layers=5 / coord_type='grid'。"""
    reg.get("st_vicinity").handler(dataset_ref="abc123")
    args, _ = runner.run.call_args
    assert args[1]["max_layers"] == 5
    assert args[1]["coord_type"] == "grid"


def test_st_vicinity_ok_stripped(runner, reg):
    """emit 的 ok 键被 handler 摘除（l3 全家同口径）。"""
    out = reg.get("st_vicinity").handler(dataset_ref="abc123")
    assert "ok" not in out
    assert out["n_tumor"] == 72


def test_st_vicinity_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "ST_VICINITY_NO_SEED", "obs 缺 is_malignant 列")
    out = reg.get("st_vicinity").handler(dataset_ref="abc123")
    assert out == {"error_code": "ST_VICINITY_NO_SEED",
                   "error_message": "obs 缺 is_malignant 列"}
