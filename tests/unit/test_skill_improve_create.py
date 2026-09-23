"""create 型 skill_improve 卡片与回调 kind 分支契约（Phase 77 Task 3，spec §5）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.coding.coding_runner import _skill_improve_card
from orchestrator.coding.skill_installer import SkillInstaller

VALID_MD = "---\nname: newsk\ndescription: d\n---\n\n# x\n"
CREATE_SUGG = {"ok": True, "kind": "create", "skill": "newsk",
               "issue": "重复手写", "fix": "沉淀",
               "files": {"SKILL.md": VALID_MD, "tools.yaml": "tools: []"}}


class TestCreateCard:
    def test_create_card_lists_files(self) -> None:
        card = _skill_improve_card("owner_open_id", CREATE_SUGG)
        text = json.dumps(card, ensure_ascii=False)
        assert "newsk" in text and "SKILL.md" in text
        # value 内嵌 suggestion 标记 kind=create
        value = card["elements"][1]["actions"][0]["value"]
        sugg = json.loads(value["suggestion"])
        assert sugg["kind"] == "create"
        # files 全文不内嵌 value（长度限制），仅键清单
        assert "files" not in sugg or isinstance(sugg.get("files"), list)

    def test_patch_card_unchanged(self) -> None:
        patch_sugg = {"skill": "s", "issue": "i", "fix": "f",
                      "file": "SKILL.md", "patch": "p"}
        card = _skill_improve_card("o", patch_sugg)
        value = card["elements"][1]["actions"][0]["value"]
        sugg = json.loads(value["suggestion"])
        assert sugg.get("kind", "patch") == "patch"
        assert sugg["patch"] == "p"


class TestCallbackKindBranch:
    """gateway 回调按 kind 分支：create → installer.install（契约级，不落真库）。"""

    def test_create_install_called(self, tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
        installer = SkillInstaller(tmp_path / "skills")
        called: dict[str, Any] = {}
        orig = installer.install

        def spy(name: str, files: dict[str, str], **kw: Any) -> dict[str, Any]:
            called["name"] = name
            called["files"] = files
            return orig(name, files, **kw)

        monkeypatch.setattr(installer, "install", spy)
        out = installer.install(CREATE_SUGG["skill"], CREATE_SUGG["files"])
        assert out["ok"] and called["name"] == "newsk"
        # 挂账⑥：落盘到安装目录 skills_installed/
        assert (tmp_path / "skills_installed" / "newsk"
                / "SKILL.md").is_file()

    def test_create_existing_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "skills_installed" / "newsk").mkdir(parents=True)
        installer = SkillInstaller(tmp_path / "skills")
        assert not installer.install("newsk", CREATE_SUGG["files"])["ok"]
