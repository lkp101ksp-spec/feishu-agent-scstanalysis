"""sc_cytosig 注册测试（mock BioRunner，不发 docker）。"""
from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_singlecell import _SC_CYTOSIG_TIMEOUT, register_l3_singlecell
from orchestrator.tools.tool_registry import ToolRegistry


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.run.return_value = {
        "ok": True, "dataset_ref": "oscc_proc_1", "mode": "diff",
        "n_factors": 43, "n_samples": 8,
        "products": {"score_csv": "/out/cytosig_scores.csv"},
        "top_by_beta": {"A": ["TGFB1", "IL6"]},
    }
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_singlecell(registry, runner)
    return registry


def test_sc_cytosig_registered(reg):
    """第 30 个 sc_* 工具：L1_compute、timeout 1800（附录 A 档）、
    浅层 schema（Phase 70 教训：无嵌套 items）、mode 枚举 + nrand 范围。"""
    spec = reg.get("sc_cytosig")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 1800 == _SC_CYTOSIG_TIMEOUT
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["dataset_ref", "groupby"]
    assert props["mode"]["enum"] == ["diff", "per_cell"]
    assert props["mode"]["default"] == "diff"
    assert props["nrand"]["minimum"] == 100
    assert props["nrand"]["maximum"] == 5000
    assert props["nrand"]["default"] == 1000
    # 浅层 schema：所有参数都是标量，无嵌套 object/array
    for p in props.values():
        assert p["type"] in ("string", "integer", "number", "boolean")


def test_sc_cytosig_dispatches(runner, reg):
    """四参数透传（dataset_ref→dataset_id 容器侧键名）。"""
    out = reg.get("sc_cytosig").handler(
        dataset_ref="oscc_proc_1", groupby="celltype",
        mode="per_cell", nrand=500)
    args, kw = runner.run.call_args
    assert args[0] == "cytosig"
    assert args[1] == {"dataset_id": "oscc_proc_1", "groupby": "celltype",
                       "mode": "per_cell", "nrand": 500}
    assert kw["timeout_sec"] == _SC_CYTOSIG_TIMEOUT
    assert out["n_factors"] == 43
    assert "ok" not in out


def test_sc_cytosig_defaults(runner, reg):
    """可选参数默认：mode=diff、nrand=1000。"""
    reg.get("sc_cytosig").handler(dataset_ref="d1", groupby="leiden")
    args, _ = runner.run.call_args
    assert args[1]["mode"] == "diff"
    assert args[1]["nrand"] == 1000


def test_sc_cytosig_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出（CYTOSIG_LOW_OVERLAP 等容器侧码）。"""
    runner.run.side_effect = BioRunError(
        "CYTOSIG_LOW_OVERLAP", "signature overlap 320 < 500")
    out = reg.get("sc_cytosig").handler(
        dataset_ref="d1", groupby="celltype")
    assert out == {"error_code": "CYTOSIG_LOW_OVERLAP",
                   "error_message": "signature overlap 320 < 500"}
