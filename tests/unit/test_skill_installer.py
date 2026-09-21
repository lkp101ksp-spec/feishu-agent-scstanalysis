"""SkillInstaller 单测（Phase 77 Task 1，spec §5）。"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from orchestrator.coding.skill_installer import SkillInstaller

VALID_MD = "---\nname: demo\ndescription: d\n---\n\n# demo\n"


@pytest.fixture()
def inst(tmp_path: Path) -> SkillInstaller:
    return SkillInstaller(tmp_path / "skills")


class TestValidateName:
    def test_legal_and_absent_ok(self, inst: SkillInstaller) -> None:
        assert inst.validate_name("new_skill")["ok"]

    def test_illegal_name_rejected(self, inst: SkillInstaller) -> None:
        for bad in ("", "Bad", "1abc", "a" * 40, "a-b", "../x"):
            assert not inst.validate_name(bad)["ok"], bad

    def test_existing_rejected(self, inst: SkillInstaller, tmp_path: Path) -> None:
        (tmp_path / "skills" / "dup").mkdir(parents=True)
        assert not inst.validate_name("dup")["ok"]


class TestValidateFiles:
    def test_valid_ok(self, inst: SkillInstaller) -> None:
        out = inst.validate_files({"SKILL.md": VALID_MD, "run_x.py": "print(1)"})
        assert out["ok"] and set(out["files"]) == {"SKILL.md", "run_x.py"}

    def test_drops_non_whitelist_keys(self, inst: SkillInstaller) -> None:
        out = inst.validate_files({"SKILL.md": VALID_MD, "evil.sh": "x",
                                   "max_retries": "3"})
        assert out["ok"] and "evil.sh" in out["dropped"]

    def test_missing_skill_md_rejected(self, inst: SkillInstaller) -> None:
        assert not inst.validate_files({"run_x.py": "print(1)"})["ok"]

    def test_missing_frontmatter_field_rejected(self, inst: SkillInstaller) -> None:
        md = "---\nname: demo\n---\n\n# x\n"  # 缺 description
        assert not inst.validate_files({"SKILL.md": md})["ok"]


class TestValidateZip:
    def _mkzip(self, tmp_path: Path, members: dict[str, str]) -> Path:
        zp = tmp_path / "s.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            for name, content in members.items():
                zf.writestr(name, content)
        return zp

    def test_valid_zip_ok(self, inst: SkillInstaller, tmp_path: Path) -> None:
        zp = self._mkzip(tmp_path, {"demo/SKILL.md": VALID_MD,
                                    "demo/run_x.py": "print(1)"})
        assert inst.validate_zip(zp)["ok"]

    def test_path_traversal_rejected(self, inst: SkillInstaller,
                                     tmp_path: Path) -> None:
        zp = self._mkzip(tmp_path, {"../evil/SKILL.md": VALID_MD})
        assert not inst.validate_zip(zp)["ok"]

    def test_missing_skill_md_rejected(self, inst: SkillInstaller,
                                       tmp_path: Path) -> None:
        zp = self._mkzip(tmp_path, {"demo/run_x.py": "print(1)"})
        assert not inst.validate_zip(zp)["ok"]


class TestInstall:
    def test_create_writes_files(self, inst: SkillInstaller) -> None:
        out = inst.install("demo", {"SKILL.md": VALID_MD})
        assert out["ok"]
        assert (Path(out["dir"]) / "SKILL.md").read_text() == VALID_MD

    def test_overwrite_backs_up(self, inst: SkillInstaller,
                                tmp_path: Path) -> None:
        d = tmp_path / "skills" / "demo"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("old", encoding="utf-8")
        out = inst.install("demo", {"SKILL.md": VALID_MD}, overwrite=True)
        assert out["ok"] and out["backup"]
        assert (d / "SKILL.md").read_text() == VALID_MD
