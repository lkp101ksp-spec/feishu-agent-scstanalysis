"""sc_genescore 注册测试（mock BioRunner，不发 docker）。"""

from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_singlecell import _SC_GENESCORE_TIMEOUT, register_l3_singlecell
from orchestrator.tools.tool_registry import ToolRegistry


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {
        "ok": True,
        "dataset_ref": "oscc_proc_1",
        "groupby": "leiden",
        "method": "progeny_mlm",
        "n_cells": 30,
        "n_pathways_scored": 14,
        "products": {"scores_csv": "/out/progeny_scores.csv"},
        "top_pathways": [{"pathway": "TGFb", "variance": 1.2}],
    }
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_singlecell(registry, runner)
    return registry


def test_sc_genescore_registered(reg):
    """第 31 个 sc_* 工具：L1_compute、timeout 1800（附录 A 档）、
    浅层 schema、required 仅 dataset_ref（groupby/top_n 有默认）。"""
    spec = reg.get("sc_genescore")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 1800 == _SC_GENESCORE_TIMEOUT
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref"]
    assert props["groupby"]["default"] == "leiden"
    assert props["top_n"]["default"] == 14
    assert props["top_n"]["minimum"] == 3
    assert props["top_n"]["maximum"] == 14
    # 浅层 schema：所有参数都是标量，无嵌套 object/array
    for p in props.values():
        assert p["type"] in ("string", "integer", "number", "boolean")


def test_sc_genescore_dispatches(runner, reg):
    """三参数透传（dataset_ref→dataset_id 容器侧键名）。"""
    out = reg.get("sc_genescore").handler(dataset_ref="oscc_proc_1", groupby="celltype", top_n=10)
    args, kw = runner.run.call_args
    assert args[0] == "genescore"
    assert args[1] == {"dataset_id": "oscc_proc_1", "groupby": "celltype", "top_n": 10}
    assert kw["timeout_sec"] == _SC_GENESCORE_TIMEOUT
    assert out["n_pathways_scored"] == 14
    assert "ok" not in out


def test_sc_genescore_defaults(runner, reg):
    """可选参数默认：groupby=leiden、top_n=14。"""
    reg.get("sc_genescore").handler(dataset_ref="d1")
    args, _ = runner.run.call_args
    assert args[1]["groupby"] == "leiden"
    assert args[1]["top_n"] == 14


def test_sc_genescore_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出（GENESCORE_LOW_OVERLAP 等容器侧码）。"""
    runner.run.side_effect = BioRunError("GENESCORE_LOW_OVERLAP", "PROGENy overlap 42 < 100")
    out = reg.get("sc_genescore").handler(dataset_ref="d1")
    assert out == {"error_code": "GENESCORE_LOW_OVERLAP", "error_message": "PROGENy overlap 42 < 100"}
