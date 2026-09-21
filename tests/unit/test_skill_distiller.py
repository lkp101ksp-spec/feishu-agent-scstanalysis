"""SkillDistiller 单测（Phase 77 Task 2，spec §5）。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.coding.skill_distiller import SkillDistiller


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def chat(self, _msgs: Any) -> str:
        self.calls += 1
        return self.reply


def _loop(events: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(status="final", tools_disabled=False,
                           tool_events=events)


def _run_cmd_events(n: int, cmd: str = "python") -> list[dict[str, Any]]:
    return [{"step": i, "name": "run_cmd", "ok": True,
             "args": f"{cmd} script{i}.py"} for i in range(n)]


WORTH_JSON = ('{"worth": true, "skill": "newsk", "issue": "重复 python x.py",'
              ' "fix": "沉淀复用", "files": {"SKILL.md":'
              ' "---\\nname: newsk\\ndescription: d\\n---\\n",'
              ' "tools.yaml": "tools: []"}}')


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    return tmp_path / "skills"


class TestTriggerFilter:
    def test_below_threshold_no_llm(self, skills_dir: Path) -> None:
        llm = _FakeLLM(WORTH_JSON)
        d = SkillDistiller(llm, skills_dir)
        out = d.distill(_loop(_run_cmd_events(1)), "task")
        assert not out["ok"] and llm.calls == 0

    def test_threshold_triggers_llm(self, skills_dir: Path) -> None:
        llm = _FakeLLM(WORTH_JSON)
        d = SkillDistiller(llm, skills_dir)
        out = d.distill(_loop(_run_cmd_events(3)), "task")
        assert llm.calls == 1 and out["ok"] and out["kind"] == "create"

    def test_skill_tool_calls_not_counted(self, skills_dir: Path) -> None:
        (skills_dir / "bioqc").mkdir(parents=True)
        (skills_dir / "bioqc" / "tools.yaml").write_text(
            "tools:\n  - name: run_qc\n", encoding="utf-8")
        events = [{"step": 0, "name": "run_qc", "ok": True, "args": "x"},
                  {"step": 1, "name": "run_cmd", "ok": True, "args": "python a.py"}]
        llm = _FakeLLM(WORTH_JSON)
        d = SkillDistiller(llm, skills_dir)
        assert not d.distill(_loop(events), "task")["ok"] and llm.calls == 0


class TestSchema:
    def test_not_worth(self, skills_dir: Path) -> None:
        d = SkillDistiller(_FakeLLM('{"worth": false}'), skills_dir)
        assert not d.distill(_loop(_run_cmd_events(3)), "t")["ok"]

    def test_missing_fields(self, skills_dir: Path) -> None:
        d = SkillDistiller(_FakeLLM('{"worth": true, "skill": "x"}'), skills_dir)
        out = d.distill(_loop(_run_cmd_events(3)), "t")
        assert not out["ok"] and "missing fields" in out["reason"]

    def test_existing_name_rejected(self, skills_dir: Path) -> None:
        (skills_dir / "newsk").mkdir(parents=True)
        d = SkillDistiller(_FakeLLM(WORTH_JSON), skills_dir)
        assert not d.distill(_loop(_run_cmd_events(3)), "t")["ok"]

    def test_bad_files_rejected(self, skills_dir: Path) -> None:
        bad = ('{"worth": true, "skill": "okname", "issue": "i", "fix": "f",'
               ' "files": {"run_x.py": "print(1)"}}')  # 缺 SKILL.md
        d = SkillDistiller(_FakeLLM(bad), skills_dir)
        assert not d.distill(_loop(_run_cmd_events(3)), "t")["ok"]
