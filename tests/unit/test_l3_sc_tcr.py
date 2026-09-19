"""sc_tcr 注册测试（mock BioRunner，不发 docker）。"""
from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_singlecell import _SC_TCR_TIMEOUT, register_l3_singlecell
from orchestrator.tools.tool_registry import ToolRegistry

FILES = [{"file": "D:/vdj/p1_tumor.csv.gz", "patient": "P1",
          "tissue": "Tumor"},
         {"file": "D:/vdj/p1_pbmc.csv.gz", "patient": "P1",
          "tissue": "PBMC"}]


@pytest.fixture
def runner():
    r = MagicMock(spec=BioRunner)
    r.resolve_data_path.side_effect = (
        lambda p: ("I:/bio_data", "vdj/" + p.split("/")[-1],
                   f"I:/bio_data/vdj/{p.split('/')[-1]}"))
    r.run.return_value = {"ok": True, "dataset_ref": "tcr_P1-P2_4",
                          "n_cells_kept": 25, "n_clonotypes": 9}
    return r


@pytest.fixture
def reg(runner, tmp_path):
    registry = ToolRegistry()
    register_l3_singlecell(registry, runner)
    return registry


def test_sc_tcr_registered(reg):
    """sc_* 第 29 工具：L1_compute、timeout 1200（附录 A 档）、
    contig_files 必填且为 array of object。"""
    spec = reg.get("sc_tcr")
    assert spec is not None
    assert spec.risk_level == "L1_compute"
    assert spec.timeout_sec == 1200 == _SC_TCR_TIMEOUT
    props = spec.parameters["properties"]
    assert spec.parameters["required"] == ["contig_files"]
    assert props["contig_files"]["type"] == "array"
    assert props["contig_files"]["minItems"] == 1
    items = props["contig_files"]["items"]
    assert items["type"] == "object"
    assert items["required"] == ["file", "patient", "tissue"]


def test_sc_tcr_dispatches(runner, reg):
    """Phase 70：list[dict] 逐元素透传（file 转容器内相对路径 +
    patient/tissue strip），/data 挂载按根去重。"""
    out = reg.get("sc_tcr").handler(contig_files=FILES)
    args, kw = runner.run.call_args
    assert args[0] == "tcr"
    assert args[1]["contig_files"] == [
        {"file": "vdj/p1_tumor.csv.gz", "patient": "P1", "tissue": "Tumor"},
        {"file": "vdj/p1_pbmc.csv.gz", "patient": "P1", "tissue": "PBMC"}]
    assert kw["mounts"] == [("I:/bio_data", "/data")]
    assert kw["timeout_sec"] == _SC_TCR_TIMEOUT
    assert out["dataset_ref"] == "tcr_P1-P2_4"
    assert "ok" not in out


def test_sc_tcr_defaults(runner, reg):
    """可选参数默认：tissue1/tissue2/dataset_ref 空串透传。"""
    reg.get("sc_tcr").handler(contig_files=FILES[:1])
    args, _ = runner.run.call_args
    assert args[1]["tissue1"] == ""
    assert args[1]["tissue2"] == ""
    assert args[1]["dataset_ref"] == ""


def test_sc_tcr_element_missing_field(runner, reg):
    """元素缺三元组字段 → INVALID_INPUT（结构化而非 KeyError）。"""
    out = reg.get("sc_tcr").handler(
        contig_files=[{"file": "a.csv", "patient": "P1"}])
    assert out == {"error_code": "INVALID_INPUT",
                   "error_message": ("contig_files elements must be "
                                     "{file, patient, tissue} objects")}


def test_sc_tcr_multi_root_rejected(runner, reg):
    """文件跨多个数据根 → INVALID_INPUT（多 -v 同目标 /data 会互相
    覆盖，容器内只能安全挂一个根）。"""
    calls = [("I:/bio_data", "vdj/a.csv", "I:/bio_data/vdj/a.csv"),
             ("E:/other_data", "vdj/b.csv", "E:/other_data/vdj/b.csv")]
    runner.resolve_data_path.side_effect = lambda p: calls[
        0 if "a.csv" in p else 1]
    out = reg.get("sc_tcr").handler(contig_files=[
        {"file": "D:/x/a.csv", "patient": "P1", "tissue": "Tumor"},
        {"file": "E:/y/b.csv", "patient": "P2", "tissue": "Tumor"}])
    assert out["error_code"] == "INVALID_INPUT"
    assert "multiple data roots" in out["error_message"]


def test_sc_tcr_error_passthrough(runner, reg):
    """BioRunError → 统一错误输出。"""
    runner.run.side_effect = BioRunError(
        "TCR_NO_VALID_CELLS", "no cells survived filtering")
    out = reg.get("sc_tcr").handler(contig_files=FILES)
    assert out == {"error_code": "TCR_NO_VALID_CELLS",
                   "error_message": "no cells survived filtering"}
