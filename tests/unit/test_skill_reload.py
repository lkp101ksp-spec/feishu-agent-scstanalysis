"""SkillLoader.reload_skill 热刷新单测（2026-09-22 挂账②：改 skill 免重启）。"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orchestrator.coding.skill_loader import SkillLoader
from orchestrator.tools.tool_registry import ToolRegistry
from shared.errors import ToolNotFoundError

MD = "---\nname: {name}\ndescription: d\n---\n\n# {name}\n"

V1 = 'tools:\n  - name: run_a\n    description: v1\n    command: ["python", "a.py"]\n'
V2 = ('tools:\n  - name: run_a\n    description: v2\n'
      '    command: ["python", "b.py"]\n    timeout_sec: 600\n')
V3 = 'tools:\n  - name: run_b\n    description: nb\n    command: ["python", "b.py"]\n'


def _write_skill(root: Path, tools_yaml: str, name: str = "demo") -> None:
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(MD.format(name=name), encoding="utf-8")
    (d / "tools.yaml").write_text(tools_yaml, encoding="utf-8")


@pytest.fixture()
def env(tmp_path: Path) -> tuple[SkillLoader, ToolRegistry]:
    return SkillLoader(tmp_path / "skills"), ToolRegistry()


class TestReloadSkill:
    def test_new_skill_registers(self, env: tuple[SkillLoader, ToolRegistry],
                                 tmp_path: Path) -> None:
        loader, reg = env
        _write_skill(tmp_path, V1)
        out = loader.reload_skill(reg, "demo")
        assert out["added"] == ["run_a"] and out["removed"] == []
        assert reg.get("run_a").description == "[skill:demo] v1"

    def test_changed_yaml_replaces(self, env: tuple[SkillLoader, ToolRegistry],
                                   tmp_path: Path) -> None:
        loader, reg = env
        _write_skill(tmp_path, V1)
        loader.reload_skill(reg, "demo")
        _write_skill(tmp_path, V2)  # 同工具名改 command/timeout
        out = loader.reload_skill(reg, "demo")
        assert out["removed"] == ["run_a"] and out["added"] == ["run_a"]
        spec = reg.get("run_a")
        assert spec.description == "[skill:demo] v2" and spec.timeout_sec == 600

    def test_removed_tool_unregistered(
            self, env: tuple[SkillLoader, ToolRegistry], tmp_path: Path) -> None:
        loader, reg = env
        _write_skill(tmp_path, V1)
        loader.reload_skill(reg, "demo")
        _write_skill(tmp_path, V3)  # run_a 删除、run_b 新增
        out = loader.reload_skill(reg, "demo")
        assert out["removed"] == ["run_a"] and out["added"] == ["run_b"]
        with pytest.raises(ToolNotFoundError):
            reg.get("run_a")
        assert reg.get("run_b").description == "[skill:demo] nb"

    def test_other_skill_untouched(self, env: tuple[SkillLoader, ToolRegistry],
                                   tmp_path: Path) -> None:
        loader, reg = env
        _write_skill(tmp_path, V1, name="demo")
        _write_skill(tmp_path, V3.replace("run_b", "run_c"), name="other")
        loader.reload_skill(reg, "demo")
        loader.reload_skill(reg, "other")
        _write_skill(tmp_path, V2)  # 只改 demo
        loader.reload_skill(reg, "demo")
        assert reg.get("run_c").description.startswith("[skill:other]")

    def test_deleted_skill_dir_clears_tools(
            self, env: tuple[SkillLoader, ToolRegistry], tmp_path: Path) -> None:
        loader, reg = env
        _write_skill(tmp_path, V1)
        loader.reload_skill(reg, "demo")
        shutil.rmtree(tmp_path / "skills" / "demo")
        out = loader.reload_skill(reg, "demo")
        assert out["removed"] == ["run_a"] and out["added"] == []
