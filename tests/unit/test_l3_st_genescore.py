"""st_genescore 注册测试（mock BioRunner，不发 docker）。"""

from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_spatial import (
    _ST_GENESCORE_TIMEOUT,
    register_l3_spatial,
)
from orchestrator.tools.tool_registry import ToolRegistry


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {
        "ok": True,
        "dataset_ref": "oscc_1",
        "groupby": "spatial_domain",
        "method": "progeny_mlm",
        "n_spots": 2500,
        "n_pathways_scored": 14,
        "n_domains": 6,
        "top_by_group": {"D1": ["TGFb", "EGFR", "MAPK"]},
        "products": {
            "scores_csv": "/ws/oscc/st_genescore/st_progeny_scores.csv",
            "spatial_png": "/ws/oscc/st_genescore/st_progeny_spatial.png",
        },
    }
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_genescore_registered(reg):
    """第 19 个 st_* 工具：L1_compute、timeout 1800（附录 A 档）、
    浅层 schema、required 仅 dataset_ref、默认 groupby=spatial_domain。"""
    spec = reg.get("st_genescore")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 1800 == _ST_GENESCORE_TIMEOUT
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["groupby"]["default"] == "spatial_domain"
    assert props["top_n"]["default"] == 14
    assert props["top_n"]["minimum"] == 3
    assert props["top_n"]["maximum"] == 14
    for p in props.values():
        assert p["type"] in ("string", "integer", "number", "boolean")


def test_st_genescore_dispatches_bio_image(runner, reg):
    """跨镜像分发（bio 镜像 + /opt/sc_tools，st_integrate 先例）+
    三参数透传（dataset_ref→dataset_id 容器侧键名）。"""
    out = reg.get("st_genescore").handler(
        dataset_ref="oscc", groupby="cluster_annotations", top_n=10)
    args, kw = runner.run.call_args
    assert args[0] == "st_genescore"
    assert args[1] == {"dataset_id": "oscc",
                       "groupby": "cluster_annotations", "top_n": 10}
    assert kw["image"] == "feishu-research-agent/bio:cpu-latest"
    assert kw["script_dir"] == "/opt/sc_tools"
    assert kw["timeout_sec"] == _ST_GENESCORE_TIMEOUT
    assert out["n_pathways_scored"] == 14
    assert "ok" not in out


def test_st_genescore_defaults(runner, reg):
    """可选参数默认：groupby=spatial_domain、top_n=14。"""
    reg.get("st_genescore").handler(dataset_ref="d1")
    args, _ = runner.run.call_args
    assert args[1]["groupby"] == "spatial_domain"
    assert args[1]["top_n"] == 14


def test_st_genescore_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出（INVALID_INPUT/GENESCORE_* 容器侧码）。"""
    runner.run.side_effect = BioRunError(
        "INVALID_INPUT", "缺 obsm['spatial']")
    out = reg.get("st_genescore").handler(dataset_ref="d1")
    assert out == {"error_code": "INVALID_INPUT",
                   "error_message": "缺 obsm['spatial']"}
