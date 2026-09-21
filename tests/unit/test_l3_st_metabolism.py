"""st_metabolism 注册测试（mock BioRunner，不发 docker）。"""

from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_spatial import (
    _ST_METABOLISM_TIMEOUT,
    register_l3_spatial,
)
from orchestrator.tools.tool_registry import ToolRegistry


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {
        "ok": True,
        "dataset_ref": "oscc_1",
        "species": "human",
        "method": "aucell",
        "groupby": "spatial_domain",
        "method_note": "AUCell 排名打分",
        "n_spots": 2500,
        "n_pathways_scored": 320,
        "n_domains": 6,
        "top_by_group": {"D1": ["Glycolysis / Gluconeogenesis"]},
        "products": {
            "scores_csv": "/ws/oscc/st_metabolism/st_metabolism_scores.csv",
            "spatial_png": "/ws/oscc/st_metabolism/st_metabolism_spatial.png",
        },
    }
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_metabolism_registered(reg):
    """第 20 个 st_* 工具：L1_compute、timeout 1800、species 枚举
    human|mouse 且默认 human（spec §3.4，无空串自动探测）。"""
    spec = reg.get("st_metabolism")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 1800 == _ST_METABOLISM_TIMEOUT
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["method"]["enum"] == ["aucell", "mean"]
    assert props["method"]["default"] == "aucell"
    assert props["groupby"]["default"] == "spatial_domain"
    assert props["species"]["enum"] == ["human", "mouse"]
    assert props["species"]["default"] == "human"
    for p in props.values():
        assert p["type"] in ("string", "integer", "number", "boolean")


def test_st_metabolism_dispatches_bio_image(runner, reg):
    """跨镜像分发 + 四参数透传。"""
    out = reg.get("st_metabolism").handler(
        dataset_ref="oscc", method="mean", groupby="cluster_annotations",
        species="mouse")
    args, kw = runner.run.call_args
    assert args[0] == "st_metabolism"
    assert args[1] == {"dataset_id": "oscc", "method": "mean",
                       "groupby": "cluster_annotations", "species": "mouse"}
    assert kw["image"] == "feishu-research-agent/bio:cpu-latest"
    assert kw["script_dir"] == "/opt/sc_tools"
    assert kw["timeout_sec"] == _ST_METABOLISM_TIMEOUT
    assert out["n_pathways_scored"] == 320
    assert "ok" not in out


def test_st_metabolism_defaults(runner, reg):
    """可选参数默认：method=aucell、groupby=spatial_domain、species=human。"""
    reg.get("st_metabolism").handler(dataset_ref="d1")
    args, _ = runner.run.call_args
    assert args[1]["method"] == "aucell"
    assert args[1]["groupby"] == "spatial_domain"
    assert args[1]["species"] == "human"


def test_st_metabolism_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "INVALID_INPUT", "groupby 'x' 仅 1 个域（<2）")
    out = reg.get("st_metabolism").handler(dataset_ref="d1")
    assert out == {"error_code": "INVALID_INPUT",
                   "error_message": "groupby 'x' 仅 1 个域（<2）"}
