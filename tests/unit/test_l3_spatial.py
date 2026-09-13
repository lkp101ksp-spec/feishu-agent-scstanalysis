"""Phase 21 st_* 工具注册测试（mock BioRunner，不发 docker）。"""
from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunError, BioRunner
from orchestrator.tools.builtin.l3_spatial import register_l3_spatial
from orchestrator.tools.tool_registry import ToolRegistry

ST_IMAGE = "feishu-research-agent/bio:st-cpu-latest"


@pytest.fixture
def runner():
    """MagicMock BioRunner：resolve_data_path 返回三元组。"""
    r = MagicMock(spec=BioRunner)
    r.resolve_data_path.return_value = (
        "I:/bio_data", "visium_out", "I:/bio_data/visium_out")
    r.run.return_value = {"ok": True, "dataset_ref": "abc123",
                          "n_spots": 200, "n_genes": 300}
    return r


@pytest.fixture
def reg(runner):
    registry = ToolRegistry()
    register_l3_spatial(registry, runner)
    return registry


def test_st_registers_fourteen_tools(reg):
    """st_* 14 工具全部注册为 L1_compute（Phase 51 +st_trajectory）。"""
    names = sorted(t.name for t in reg.list())
    assert names == ["st_cnv", "st_commot", "st_deconvolve", "st_domains",
                     "st_load", "st_markers", "st_misty", "st_niche",
                     "st_plot", "st_process", "st_qc", "st_stats",
                     "st_trajectory", "st_vicinity"]
    for t in reg.list():
        assert t.risk_level == "L1_compute"


def test_st_load_uses_dir_dataset_id_and_st_image(runner, reg, monkeypatch):
    """st_load：目录聚合 dataset_id + run 指定 st 镜像与 /opt/st_tools。

    compute_dataset_id_dir 打桩：mock 路径本机不存在，真实实现会抛
    SC_FILE_NOT_FOUND（已由 test_st_load_path_forbidden 覆盖错误分支）。
    """
    seen = []

    def fake_dir_dataset_id(host):
        seen.append(host)
        return "dirhash456"

    monkeypatch.setattr(
        "orchestrator.tools.builtin.l3_spatial.compute_dataset_id_dir",
        fake_dir_dataset_id)
    out = reg.get("st_load").handler(path="I:/bio_data/visium_out")
    assert out["dataset_ref"] == "abc123"
    assert seen == ["I:/bio_data/visium_out"]
    args, kwargs = runner.run.call_args
    assert args[0] == "load"
    assert args[1]["dataset_id"] == "dirhash456"
    assert kwargs.get("image") == ST_IMAGE
    assert kwargs.get("script_dir") == "/opt/st_tools"
    assert kwargs["mounts"] == [("I:/bio_data", "/data")]
    assert "ok" not in out


def test_st_load_path_forbidden(runner, reg):
    """白名单外路径 → SC_PATH_FORBIDDEN 错误输出（不抛异常）。"""
    runner.resolve_data_path.side_effect = BioRunError(
        "SC_PATH_FORBIDDEN", "outside allowed roots")
    out = reg.get("st_load").handler(path="C:/evil")
    assert out == {"error_code": "SC_PATH_FORBIDDEN",
                   "error_message": "outside allowed roots"}


def test_st_load_h5ad_file_uses_file_dataset_id(runner, reg, monkeypatch):
    """st_load：.h5ad 文件路径用文件级 dataset_id（真机验收发现的修复）。

    修复前 handler 无条件 compute_dataset_id_dir（目录聚合 hash），
    h5ad 文件路径在宿主侧即抛 SC_FILE_NOT_FOUND；load.py 容器脚本
    本就支持 .h5ad（suffix 探测）——分支口径与之一致。
    """
    seen = []
    runner.resolve_data_path.return_value = (
        "I:/bio_data", "oscc.h5ad", "I:/bio_data/oscc.h5ad")

    def fake_file_dataset_id(host):
        seen.append(host)
        return "filehash789"

    monkeypatch.setattr(
        "orchestrator.tools.builtin.l3_spatial.compute_dataset_id",
        fake_file_dataset_id)
    out = reg.get("st_load").handler(path="I:/bio_data/oscc.h5ad")
    assert out["dataset_ref"] == "abc123"
    assert seen == ["I:/bio_data/oscc.h5ad"]
    args, _ = runner.run.call_args
    assert args[1]["dataset_id"] == "filehash789"


def test_st_qc_params_passthrough(runner, reg):
    """st_qc 参数透传到容器脚本。"""
    reg.get("st_qc").handler(dataset_ref="abc123", min_genes=10,
                             max_genes=8000, max_mt_pct=30.0)
    args, kwargs = runner.run.call_args
    assert args[0] == "qc"
    assert args[1] == {"dataset_id": "abc123", "min_genes": 10,
                       "max_genes": 8000, "max_mt_pct": 30.0}
    assert kwargs.get("image") == ST_IMAGE


def test_st_plot_genes_max_six(reg):
    """st_plot genes 参数 schema maxItems=6。"""
    spec = reg.get("st_plot")
    genes_prop = spec.parameters["properties"]["genes"]
    assert genes_prop["maxItems"] == 6


def test_st_plot_genes_repr_string_parsed(runner, reg):
    """planner 把 genes 序列化成 repr 串时宽松解析（真机发现）。"""
    reg.get("st_plot").handler(dataset_ref="abc123",
                               genes="['MARKER_D1_0', 'MARKER_D2_1']")
    args, _ = runner.run.call_args
    assert args[1]["genes"] == ["MARKER_D1_0", "MARKER_D2_1"]


def test_st_plot_genes_plain_string_wraps_list(runner, reg):
    """纯字符串基因名包装为单元素 list（宽容单基因场景）。"""
    reg.get("st_plot").handler(dataset_ref="abc123", genes="MARKER_D1_0")
    args, _ = runner.run.call_args
    assert args[1]["genes"] == ["MARKER_D1_0"]


def test_register_fourteen_st_tools(reg):
    """Phase 51 后 st_* 共 14 工具（13 + st_trajectory）。"""
    names = sorted(t.name for t in reg.list() if t.name.startswith("st_"))
    assert names == [
        "st_cnv", "st_commot", "st_deconvolve", "st_domains", "st_load",
        "st_markers", "st_misty", "st_niche", "st_plot", "st_process",
        "st_qc", "st_stats", "st_trajectory", "st_vicinity"]


def test_st_domains_forwards_params(runner, reg):
    """method/resolution 透传 + st 镜像 + /opt/st_tools。"""
    reg.get("st_domains").handler(dataset_ref="abc123",
                                  method="banksy", resolution=0.8)
    args, kw = runner.run.call_args
    assert args[0] == "domains"
    assert args[1] == {"dataset_id": "abc123", "method": "banksy",
                       "resolution": 0.8}
    assert kw["image"].endswith("st-cpu-latest")
    assert kw["script_dir"] == "/opt/st_tools"


def test_st_commot_forwards_params(runner, reg):
    """species/dis_thr 透传（默认 human/200）。"""
    reg.get("st_commot").handler(dataset_ref="abc123")
    args, kw = runner.run.call_args
    assert args[0] == "commot"
    assert args[1]["species"] == "human"
    assert args[1]["dis_thr"] == 200
    assert kw["script_dir"] == "/opt/st_tools"


def test_st_deconvolve_sc_ref_dataset_vs_path(runner, reg):
    """sc_ref 双来源：12hex 走 workspace（无挂载）；路径走白名单挂载 /data。

    12hex 场景 run 传独立超时；路径场景挂载白名单根目录到 /data。
    """
    runner.resolve_data_path = lambda p: ("I:/bio_test_data", "ref.h5ad", "x")
    reg.get("st_deconvolve").handler(
        dataset_ref="abc123456789", sc_ref="deadbeefdead",
        deconv_timeout=3600)
    args, kw = runner.run.call_args
    assert args[0] == "deconvolve"
    assert args[1]["sc_ref_dataset"] == "deadbeefdead"
    assert kw.get("mounts") is None and kw["timeout_sec"] == 3600

    reg.get("st_deconvolve").handler(
        dataset_ref="abc123456789", sc_ref="I:/bio_test_data/ref.h5ad",
        deconv_timeout=3600)
    args, kw = runner.run.call_args
    assert args[1]["sc_ref_path"] == "ref.h5ad"
    assert kw["mounts"] == [("I:/bio_test_data", "/data")]


def test_st_deconvolve_cpu_tuning_passthrough(runner, reg):
    """CPU 可行性参数透传（真实 Visium 验收挂账）：ref_epochs/
    num_samples/ref_max_cells_per_type 双分支都进容器 args。"""
    runner.resolve_data_path = lambda p: ("I:/bio_test_data", "ref.h5ad", "x")
    for sc_ref in ("deadbeefdead", "I:/bio_test_data/ref.h5ad"):
        reg.get("st_deconvolve").handler(
            dataset_ref="abc123456789", sc_ref=sc_ref,
            max_epochs=2000, ref_epochs=100, num_samples=200,
            ref_max_cells_per_type=150)
        args, _ = runner.run.call_args
        assert args[1]["ref_epochs"] == 100
        assert args[1]["num_samples"] == 200
        assert args[1]["ref_max_cells_per_type"] == 150
        assert args[1]["max_epochs"] == 2000
