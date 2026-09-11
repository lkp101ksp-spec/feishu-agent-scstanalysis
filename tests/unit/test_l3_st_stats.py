"""st_stats 注册测试（mock BioRunner，不发 docker）。"""
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
                          "analysis": "autocorr", "n_genes_tested": 50,
                          "top_gene": "G1", "top_stat": 0.42}
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_stats_registered(reg):
    """st_* 第 9 工具注册为 L1_compute，timeout 1800。"""
    spec = reg.get("st_stats")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 1800
    props = spec.parameters["properties"]
    assert props["analysis"]["enum"] == [
        "autocorr", "cooccurrence", "nhood_enrichment"]
    assert props["mode"]["enum"] == ["moran", "geary"]


def test_st_stats_forwards_params(runner, reg):
    """全参数透传 + st 镜像 + /opt/st_tools + timeout 1800。"""
    reg.get("st_stats").handler(
        dataset_ref="abc123", analysis="nhood_enrichment",
        cluster_key="spatial_domain", n_perms=500,
        coord_type="generic", n_neighs=8)
    args, kw = runner.run.call_args
    assert args[0] == "stats"
    assert args[1] == {"dataset_id": "abc123",
                       "analysis": "nhood_enrichment",
                       "mode": "moran", "genes": [],
                       "cluster_key": "spatial_domain",
                       "n_perms": 500,
                       "coord_type": "generic", "n_neighs": 8}
    assert kw["image"] == ST_IMAGE
    assert kw["script_dir"] == "/opt/st_tools"
    assert kw["timeout_sec"] == 1800


def test_st_stats_defaults(runner, reg):
    """默认值：mode moran / n_perms 1000 / grid / 6 / genes [] / key ''。"""
    reg.get("st_stats").handler(dataset_ref="abc123", analysis="autocorr")
    args, _ = runner.run.call_args
    assert args[1]["mode"] == "moran"
    assert args[1]["n_perms"] == 1000
    assert args[1]["coord_type"] == "grid"
    assert args[1]["n_neighs"] == 6
    assert args[1]["genes"] == []
    assert args[1]["cluster_key"] == ""


def test_st_stats_genes_string_parsed(runner, reg):
    """planner 把 genes 序列化成 repr 串时宽松解析（st_plot 同款纪律）。"""
    reg.get("st_stats").handler(dataset_ref="abc123", analysis="autocorr",
                                genes="['G1', 'G2']")
    args, _ = runner.run.call_args
    assert args[1]["genes"] == ["G1", "G2"]


def test_st_stats_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "ST_CLUSTER_KEY_MISSING", "no cluster col")
    out = reg.get("st_stats").handler(dataset_ref="abc123",
                                      analysis="cooccurrence")
    assert out == {"error_code": "ST_CLUSTER_KEY_MISSING",
                   "error_message": "no cluster col"}
