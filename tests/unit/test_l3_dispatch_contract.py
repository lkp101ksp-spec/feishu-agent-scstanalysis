"""L3 sc_*/st_* 全工具分发合同测试（执行面统一轮）。

钉死两条分发纪律，防"纸面超时"与"容器实际超时"漂移：
1. handler 实际传给 BioRunner.run 的 timeout_sec == ToolSpec.timeout_sec
   （缺失会静默回退 BioRunner 默认 900s——st_process/markers/domains/
   commot 曾声明 1200-1800s 却被容器 900s 提前杀）；
2. handler 正常返回（无 error_code）——保证打桩路径本身可达。

参数由 ToolSpec.parameters（JSON schema）自动合成，新增工具零改动入列。
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunner
from orchestrator.tools.builtin.l3_singlecell import register_l3_singlecell
from orchestrator.tools.builtin.l3_spatial import register_l3_spatial
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


def _fake_id(path: str) -> str:
    """compute_dataset_id 打桩：mock 路径本机不存在，真实实现会 stat 抛错。"""
    return "aaaaaaaaaaaa"


def _synth_args(spec: ToolSpec) -> dict[str, Any]:
    """按 schema required 字段合成最小合法参数（可选字段走 handler 默认）。"""
    props: dict[str, Any] = spec.parameters.get("properties", {})
    out: dict[str, Any] = {}
    for name in spec.parameters.get("required", []):
        p = props.get(name, {})
        if name in ("dataset_ref", "sc_ref"):
            # sc_ref 用 12-hex 走 st_deconvolve 的 workspace 分支
            out[name] = "aaaaaaaaaaaa"
        elif name in ("path", "bulk_file", "rna_file", "adt_file"):
            out[name] = "/data/x.h5ad"
        elif name == "genes":
            out[name] = ["GENE1"]
        elif p.get("type") == "object":
            out[name] = {"set1": ["GENE1"]}
        elif p.get("type") == "integer":
            out[name] = 1
        elif p.get("type") == "number":
            out[name] = 1.0
        elif "enum" in p:
            out[name] = p["enum"][0]
        else:
            out[name] = "x"
    return out


@pytest.fixture
def runner() -> MagicMock:
    """MagicMock BioRunner：resolve 三元组、run 恒 ok、workspace_root 空
    （空值让 sc_load 跳过既有 dataset 直通分支走 resolve 路径）。"""
    r = MagicMock(spec=BioRunner)
    r.workspace_root = ""
    r.resolve_data_path.return_value = ("/data", "x.h5ad", "/data/x.h5ad")
    r.run.return_value = {"ok": True}
    return r


@pytest.fixture
def reg(runner: MagicMock) -> ToolRegistry:
    registry = ToolRegistry()
    register_l3_singlecell(registry, runner)
    register_l3_spatial(registry, runner)
    return registry


def _all_l3_specs(reg: ToolRegistry) -> list[ToolSpec]:
    return [t for t in reg.list() if t.name.startswith(("sc_", "st_"))]


def test_l3_timeout_contract(reg: ToolRegistry, runner: MagicMock,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    """全部 sc_*/st_* 工具：run 实际 timeout_sec == spec 声明值。"""
    monkeypatch.setattr(
        "orchestrator.tools.builtin.l3_singlecell.compute_dataset_id",
        _fake_id)
    monkeypatch.setattr(
        "orchestrator.tools.builtin.l3_spatial.compute_dataset_id", _fake_id)
    monkeypatch.setattr(
        "orchestrator.tools.builtin.l3_spatial.compute_dataset_id_dir",
        _fake_id)
    specs = _all_l3_specs(reg)
    assert len(specs) >= 40  # sc 25 + st 15（后续新增自动入列）
    checked: list[str] = []
    for spec in specs:
        before = runner.run.call_count
        out = spec.handler(**_synth_args(spec))
        assert "error_code" not in out, f"{spec.name} handler 失败: {out}"
        assert runner.run.call_count == before + 1, (
            f"{spec.name} 应恰好一次 runner.run")
        kwargs = runner.run.call_args.kwargs
        assert kwargs.get("timeout_sec") == spec.timeout_sec, (
            f"{spec.name} 容器超时 {kwargs.get('timeout_sec')}s != 声明 "
            f"{spec.timeout_sec}s（缺失会静默回退 900s）")
        checked.append(spec.name)
    assert len(checked) == len(specs)
