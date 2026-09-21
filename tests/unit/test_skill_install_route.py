"""/skill install 手动入口路由单测（Phase 77 Task 4，spec §5）。"""
from __future__ import annotations

import zipfile
from pathlib import Path

from orchestrator.coding.skill_installer import SkillInstaller

VALID_MD = "---\nname: mansk\ndescription: d\n---\n\n# x\n"


def _mkzip(tmp_path: Path, members: dict[str, str]) -> Path:
    """在 tmp_path 下造一个含指定成员的 zip，返回路径。"""
    zp = tmp_path / "up.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        for n, c in members.items():
            zf.writestr(n, c)
    return zp


class TestManualInstallPath:
    def test_valid_zip_validates(self, tmp_path: Path) -> None:
        """合法 zip（单层目录 + SKILL.md + .py）通过 validate_zip。"""
        inst = SkillInstaller(tmp_path / "skills")
        zp = _mkzip(tmp_path, {"mansk/SKILL.md": VALID_MD,
                               "mansk/run_x.py": "print(1)"})
        out = inst.validate_zip(zp)
        assert out["ok"] and "SKILL.md" in out["files"]

    def test_manual_overwrite_allowed(self, tmp_path: Path) -> None:
        """手动 install（overwrite=True）允许覆盖同名 skill 且整目录备份。"""
        d = tmp_path / "skills" / "mansk"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("old", encoding="utf-8")
        inst = SkillInstaller(tmp_path / "skills")
        out = inst.install("mansk", {"SKILL.md": VALID_MD}, overwrite=True)
        assert out["ok"] and out["backup"]
        assert (d / "SKILL.md").read_text() == VALID_MD

    def test_bad_zip_rejected(self, tmp_path: Path) -> None:
        """路径穿越成员（../evil/...）被 validate_zip 拒绝。"""
        inst = SkillInstaller(tmp_path / "skills")
        zp = _mkzip(tmp_path, {"../evil/SKILL.md": VALID_MD})
        assert not inst.validate_zip(zp)["ok"]

    def test_missing_frontmatter_rejected(self, tmp_path: Path) -> None:
        """SKILL.md 缺 frontmatter 被 validate_zip 拒绝。"""
        inst = SkillInstaller(tmp_path / "skills")
        zp = _mkzip(tmp_path, {"mansk/SKILL.md": "# no frontmatter\n"})
        assert not inst.validate_zip(zp)["ok"]
