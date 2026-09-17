"""L3 sc_*/st_* 全工具分发合同测试（执行面统一轮）。

钉死三条分发纪律，防"纸面声明"与"容器实际参数"漂移：
1. handler 实际传给 BioRunner.run 的 timeout_sec == ToolSpec.timeout_sec
   （缺失会静默回退 BioRunner 默认 900s——st_process/markers/domains/
   commot 曾声明 1200-1800s 却被容器 900s 提前杀）；
2. handler 传的 cpus/memory == ToolSpec 声明（挂账③：None=不传走
   全局默认，声明了必须透传，防"纸面覆写"）；
3. handler 正常返回（无 error_code）——保证打桩路径本身可达。

参数由 ToolSpec.parameters（JSON schema）自动合成，新增工具零改动入列。
另有表守护测试：spec 附录 A 口径表 vs registry/skill 源码/settings
三方比对（挂账②），改超时必须同步表。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from orchestrator.tools.bio.bio_runner import BioRunner
from orchestrator.tools.builtin.l3_singlecell import register_l3_singlecell
from orchestrator.tools.builtin.l3_spatial import register_l3_spatial
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec

SPEC_MD = Path(__file__).resolve().parents[2] / (
    "docs/superpowers/specs/"
    "2026-09-17-execution-plane-unification-design.md")
SKILL_PY = Path(__file__).resolve().parents[2] / (
    "skills/bio_trajectory_pipeline/run_pipeline.py")


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
def reg(runner: MagicMock, tmp_path: Path) -> ToolRegistry:
    registry = ToolRegistry()
    # sc_scenic handler 要求 DB 目录真实存在（缺则 SC_CONFIG 快败，
    # CI 上曾因此挂掉合同测试——本机靠 repo 根真实 DB 目录侥幸绿）。
    # 合同测试只验分发参数不触容器，tmp 空目录即可。
    scenic_db = tmp_path / "scenic_db"
    (scenic_db / "cisTarget_databases").mkdir(parents=True)
    (scenic_db / "motifAnnotations").mkdir()
    register_l3_singlecell(
        registry, runner, bio_scenic_db_root=str(scenic_db))
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
    assert len(specs) >= 40  # sc + st（后续新增自动入列）
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
        assert kwargs.get("cpus") == spec.cpus, (
            f"{spec.name} 容器 cpus {kwargs.get('cpus')!r} != 声明 "
            f"{spec.cpus!r}（挂账③：声明了必须透传）")
        assert kwargs.get("memory") == spec.memory, (
            f"{spec.name} 容器 memory {kwargs.get('memory')!r} != 声明 "
            f"{spec.memory!r}（挂账③：声明了必须透传）")
        checked.append(spec.name)
    assert len(checked) == len(specs)


def _parse_spec_table(text: str) -> dict[str, int]:
    """解析附录 A L3 分组表 → {工具名: 档位秒}。

    行格式 `| 600 | sc_load, sc_qc, ... |`；st_deconvolve 带 `*`
    参数化档标记，剥掉后照常入表。
    """
    table: dict[str, int] = {}
    for m in re.finditer(
            r"^\|\s*(\d{3,4})\s*\|\s*([a-z_][a-z_0-9*, ]*)\|$", text, re.M):
        sec = int(m.group(1))
        for token in m.group(2).split(","):
            name = token.strip().rstrip("*").strip()
            if name:
                table[name] = sec
    return table


def test_spec_timeout_table_guard(reg: ToolRegistry) -> None:
    """附录 A 口径表三方守护（挂账②）：表 vs registry / skill 常量 /
    settings 默认，任一漂移即红——改超时必须同步 spec 附录 A。
    """
    from config.settings import Settings

    text = SPEC_MD.read_text(encoding="utf-8")

    # 1) L3 表 vs registry 动态值（全工具覆盖，缺一即红）
    table = _parse_spec_table(text)
    for spec in _all_l3_specs(reg):
        assert spec.name in table, (
            f"{spec.name} 未入附录 A 口径表——新增/改名工具须同步表")
        assert table[spec.name] == spec.timeout_sec, (
            f"{spec.name} 附录 A 档 {table[spec.name]}s != 声明 "
            f"{spec.timeout_sec}s")
    assert set(table) == {s.name for s in _all_l3_specs(reg)}, (
        "附录 A 表含 registry 之外的工具名（表陈旧）")

    # 2) skill 通道：表 3900 vs run_pipeline.py 常量 STEP_TIMEOUT_SEC
    m = re.search(r"STEP_TIMEOUT_SEC\s*=\s*(\d+)", SKILL_PY.read_text(
        encoding="utf-8"))
    assert m, "run_pipeline.py 缺 STEP_TIMEOUT_SEC 常量"
    skill_row = re.search(
        r"skill bio_trajectory_pipeline 每步\s*\|\s*(\d+)\s*\|", text)
    assert skill_row, "附录 A 缺 skill 通道行"
    assert int(skill_row.group(1)) == int(m.group(1)), (
        "附录 A skill 档与 STEP_TIMEOUT_SEC 常量漂移")

    # 3) 框架通道：表 vs settings 类字段默认（不实例化、不受 env 影响）
    for field, label in (
            ("research_sc_timeout_sec", "research 墙钟"),
            ("bio_script_timeout_sec", "BioRunner 兜底默认")):
        default = getattr(Settings, field)
        row = re.search(
            rf"{label}[^\n|]*\|\s*(\d+)\s*\|", text)
        assert row, f"附录 A 缺 {label} 行"
        assert int(row.group(1)) == default, (
            f"附录 A {label} 档与 settings.{field} 默认 {default} 漂移")
