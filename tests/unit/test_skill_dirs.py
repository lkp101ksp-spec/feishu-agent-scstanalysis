"""内置 skills/ 与安装 skills_installed/ 双目录约定（2026-09-23 挂账⑥）。

第三方/自动提炼 skill 装进 skills_installed/（门禁豁免），内置 skill 留在
skills/（门禁照管）；loader/diagnoser/distiller 双目录可见，installer 只装
安装目录且拒绝与内置同名（防遮蔽死重量）。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from orchestrator.coding.skill_diagnoser import SkillDiagnoser
from orchestrator.coding.skill_distiller import SkillDistiller
from orchestrator.coding.skill_installer import SkillInstaller
from orchestrator.coding.skill_loader import SkillLoader, installed_dir_for
from orchestrator.tools.tool_registry import ToolRegistry

MD = "---\nname: {n}\ndescription: {d}\n---\n\nbody\n"
TY = 'tools:\n  - name: {t}\n    command: ["python", "x.py"]\n'


def _mk(root: Path, name: str, desc: str = "d", tool: str | None = None) -> Path:
    """在 root/<name>/ 下落一个最小可用 skill（SKILL.md[+tools.yaml]）。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(MD.format(n=name, d=desc), encoding="utf-8")
    if tool:
        (d / "tools.yaml").write_text(TY.format(t=tool), encoding="utf-8")
    return d


class TestInstalledDirConvention:
    def test_installed_dir_is_sibling(self, tmp_path: Path) -> None:
        assert installed_dir_for(tmp_path / "skills") == (
            tmp_path / "skills_installed")

    def test_scan_covers_both(self, tmp_path: Path) -> None:
        _mk(tmp_path / "skills", "b1", tool="tool_b")
        _mk(tmp_path / "skills_installed", "i1", tool="tool_i")
        loader = SkillLoader(tmp_path / "skills")
        assert loader.scan() == 2
        assert {s.name for s in loader.skills} == {"b1", "i1"}

    def test_builtin_wins_on_name_clash(self, tmp_path: Path) -> None:
        _mk(tmp_path / "skills", "same", desc="builtin", tool="tool_b")
        _mk(tmp_path / "skills_installed", "same", desc="installed",
            tool="tool_i")
        loader = SkillLoader(tmp_path / "skills")
        assert loader.scan() == 1
        assert loader.skills[0].description == "builtin"

    def test_install_targets_installed_dir(self, tmp_path: Path) -> None:
        inst = SkillInstaller(tmp_path / "skills")
        out = inst.install("newsk", {"SKILL.md": MD.format(n="newsk", d="d")})
        assert out["ok"]
        assert Path(out["dir"]).parent == tmp_path / "skills_installed"
        assert not (tmp_path / "skills" / "newsk").exists()

    def test_install_rejects_builtin_name(self, tmp_path: Path) -> None:
        _mk(tmp_path / "skills", "bioqc")
        inst = SkillInstaller(tmp_path / "skills")
        out = inst.validate_name("bioqc")
        assert not out["ok"] and "bioqc" in out["error"]

    def test_reload_resolves_installed(self, tmp_path: Path) -> None:
        _mk(tmp_path / "skills_installed", "i1", tool="tool_i")
        reg = ToolRegistry()
        out = SkillLoader(tmp_path / "skills").reload_skill(reg, "i1")
        assert out["added"] == ["tool_i"]
        assert reg.get("tool_i").description.startswith("[skill:i1]")

    def test_diagnoser_tool_map_covers_installed(self, tmp_path: Path) -> None:
        _mk(tmp_path / "skills", "b1", tool="tool_b")
        _mk(tmp_path / "skills_installed", "i1", tool="tool_i")
        diag = SkillDiagnoser(llm=MagicMock(), skills_dir=tmp_path / "skills")
        m = diag._skill_tool_map()
        assert m == {"tool_b": "b1", "tool_i": "i1"}

    def test_diagnoser_apply_patches_installed(self, tmp_path: Path) -> None:
        d = _mk(tmp_path / "skills_installed", "i1")
        diag = SkillDiagnoser(llm=MagicMock(), skills_dir=tmp_path / "skills")
        out = diag.apply({"skill": "i1", "file": "SKILL.md",
                          "patch": "### 排查\n先看日志。"})
        assert out["ok"], out
        md = (d / "SKILL.md").read_text(encoding="utf-8")
        assert "改进记录" in md

    def test_distiller_existing_skills_cover_installed(
            self, tmp_path: Path) -> None:
        _mk(tmp_path / "skills", "b1", desc="内置")
        _mk(tmp_path / "skills_installed", "i1", desc="安装")
        dist = SkillDistiller(llm=MagicMock(), skills_dir=tmp_path / "skills")
        text = dist._existing_skills_text()
        assert "b1" in text and "i1" in text
