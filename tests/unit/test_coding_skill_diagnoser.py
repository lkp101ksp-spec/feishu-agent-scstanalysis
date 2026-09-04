"""SkillDiagnoser：失败轨迹诊断 + 审批写回（Phase 27 T1+T3）。"""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from orchestrator.coding.agent_loop import LoopResult
from orchestrator.coding.skill_diagnoser import SkillDiagnoser


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    """构造含一个完整 skill（SKILL.md + tools.yaml）的临时 skills 目录。"""
    d = tmp_path / "skills"
    skill = d / "bioqc"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: bioqc\ndescription: 单细胞质控\n---\n对 h5ad 做质控。\n",
        encoding="utf-8",
    )
    (skill / "tools.yaml").write_text(
        "tools:\n"
        "  - name: run_qc\n"
        "    description: 对 h5ad 执行标准 QC\n"
        "    command: [python, qc.py]\n"
        "    timeout_sec: 60\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture()
def failed_result() -> LoopResult:
    """构造一次因 skill 工具连续失败而终止的 LoopResult。"""
    return LoopResult(
        status="no_tools",
        final_text="",
        steps=4,
        approx_tokens=1200,
        abort_reason="model kept requesting tools after disable",
        tool_events=[
            {"step": 1, "name": "run_qc", "ok": False},
            {"step": 2, "name": "run_qc", "ok": False},
            {"step": 3, "name": "run_qc", "ok": False},
            {"step": 3, "name": "read_file", "ok": True},
        ],
    )


def _suggestion_json() -> str:
    """合法的 LLM 诊断返回。"""
    return json.dumps({
        "skill": "bioqc",
        "issue": "run_qc 缺少 input_path 必填参数说明",
        "fix": "在 SKILL.md 增加参数示例",
        "file": "SKILL.md",
        "patch": "### 示例\nrun_qc --input_path in.h5ad",
    }, ensure_ascii=False)


class TestDiagnose:
    def test_no_skill_involved_short_circuits_llm(self, skills_dir):
        """tool_events 无 skill 工具 → 直接返回，不调 LLM。"""
        llm = Mock()
        diag = SkillDiagnoser(llm, skills_dir)
        result = LoopResult(status="final", final_text="done", steps=1,
                            approx_tokens=100, abort_reason="",
                            tool_events=[{"step": 1, "name": "read_file", "ok": False}])
        out = diag.diagnose(result, "读个文件")
        assert out == {"ok": False, "reason": "no skill involved"}
        llm.chat.assert_not_called()

    def test_skill_failure_returns_five_fields(self, skills_dir, failed_result):
        """有 skill 失败 → LLM 合法 JSON → 返回五字段且 ok=True。"""
        llm = Mock()
        llm.chat.return_value = _suggestion_json()
        diag = SkillDiagnoser(llm, skills_dir)
        out = diag.diagnose(failed_result, "对 pbmc.h5ad 做质控")
        llm.chat.assert_called_once()
        assert out["ok"] is True
        for key in ("skill", "issue", "fix", "file", "patch"):
            assert key in out
        assert out["skill"] == "bioqc"

    def test_fenced_json_is_parsed(self, skills_dir, failed_result):
        """LLM 返回带 ```json 围栏 → 正确剥离解析。"""
        llm = Mock()
        llm.chat.return_value = "分析如下：\n```json\n" + _suggestion_json() + "\n```\n以上。"
        diag = SkillDiagnoser(llm, skills_dir)
        out = diag.diagnose(failed_result, "对 pbmc.h5ad 做质控")
        assert out["ok"] is True
        assert out["file"] == "SKILL.md"

    def test_unparseable_llm_output(self, skills_dir, failed_result):
        """LLM 返回非 JSON → {"ok": False, "reason": "parse error"}。"""
        llm = Mock()
        llm.chat.return_value = "我觉得是超时问题，但我说不清。"
        diag = SkillDiagnoser(llm, skills_dir)
        out = diag.diagnose(failed_result, "对 pbmc.h5ad 做质控")
        assert out == {"ok": False, "reason": "parse error"}


class TestApply:
    def test_apply_skill_md_appends_section_and_backup(self, skills_dir):
        """SKILL.md：追加"## 改进记录"段落 + 生成 .bak 备份。"""
        diag = SkillDiagnoser(Mock(), skills_dir)
        md_path = skills_dir / "bioqc" / "SKILL.md"
        before = md_path.read_text(encoding="utf-8")
        out = diag.apply(json.loads(_suggestion_json()))
        assert out["ok"] is True
        after = md_path.read_text(encoding="utf-8")
        assert after.startswith(before)
        assert "## 改进记录（" in after
        assert "问题：run_qc 缺少 input_path 必填参数说明" in after
        assert "### 示例" in after
        bak = md_path.with_name("SKILL.md.bak")
        assert bak.is_file()
        assert bak.read_text(encoding="utf-8") == before

    def test_apply_tools_yaml_updates_field_and_backup(self, skills_dir):
        """tools.yaml：字段更新生效 + 生成 .bak 备份。"""
        diag = SkillDiagnoser(Mock(), skills_dir)
        yaml_path = skills_dir / "bioqc" / "tools.yaml"
        before = yaml_path.read_text(encoding="utf-8")
        out = diag.apply({
            "skill": "bioqc", "issue": "超时太短", "fix": "调大 timeout",
            "file": "tools.yaml", "patch": "run_qc:\n  timeout_sec: 600\n",
        })
        assert out["ok"] is True
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert data["tools"][0]["timeout_sec"] == 600
        bak = yaml_path.with_name("tools.yaml.bak")
        assert bak.is_file()
        assert bak.read_text(encoding="utf-8") == before

    def test_apply_missing_file_returns_error(self, skills_dir):
        """目标文件不存在 → 返回错误 dict，不抛异常。"""
        diag = SkillDiagnoser(Mock(), skills_dir)
        (skills_dir / "bioqc" / "tools.yaml").unlink()
        out = diag.apply({
            "skill": "bioqc", "issue": "i", "fix": "f",
            "file": "tools.yaml", "patch": "timeout_sec: 600",
        })
        assert out["ok"] is False
        assert "not found" in out["error"]

    def test_apply_missing_skill_dir_returns_error(self, skills_dir):
        """skill 目录不存在 → 返回错误 dict，不抛异常。"""
        diag = SkillDiagnoser(Mock(), skills_dir)
        out = diag.apply({
            "skill": "ghost", "issue": "i", "fix": "f",
            "file": "SKILL.md", "patch": "内容",
        })
        assert out["ok"] is False
        assert "skill dir not found" in out["error"]

    def test_apply_empty_patch_rejected(self, skills_dir):
        """patch 为空 → 拒绝写回。"""
        diag = SkillDiagnoser(Mock(), skills_dir)
        out = diag.apply({
            "skill": "bioqc", "issue": "i", "fix": "f",
            "file": "SKILL.md", "patch": "   ",
        })
        assert out["ok"] is False
        assert out["error"] == "empty patch"
