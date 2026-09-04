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

    def test_apply_tools_yaml_filters_schema_external_fields(self, skills_dir):
        """诊断幻觉 schema 外字段（max_retries 等）→ 只写白名单字段，其余丢弃。

        真机验收发现（2026-09-04）：LLM 建议 max_retries/retry_on 等
        tools.yaml 不支持的字段，原样写入永不生效，属"假改进"。
        """
        diag = SkillDiagnoser(Mock(), skills_dir)
        yaml_path = skills_dir / "bioqc" / "tools.yaml"
        out = diag.apply({
            "skill": "bioqc", "issue": "超时太短", "fix": "调大 timeout 并加重试",
            "file": "tools.yaml",
            "patch": ("run_qc:\n  timeout_sec: 600\n  max_retries: 8\n"
                      "  retry_on:\n    - FileNotFoundError\n"),
        })
        assert out["ok"] is True
        assert out["updated"] == ["run_qc.timeout_sec"]   # 只有白名单字段生效
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        tool = data["tools"][0]
        assert tool["timeout_sec"] == 600
        assert "max_retries" not in tool and "retry_on" not in tool

    def test_apply_tools_yaml_all_external_fields_rejected(self, skills_dir):
        """patch 全是 schema 外字段 → 拒绝写回（文件不变，无 .bak）。"""
        diag = SkillDiagnoser(Mock(), skills_dir)
        yaml_path = skills_dir / "bioqc" / "tools.yaml"
        before = yaml_path.read_text(encoding="utf-8")
        out = diag.apply({
            "skill": "bioqc", "issue": "i", "fix": "f",
            "file": "tools.yaml",
            "patch": "run_qc:\n  max_retries: 8\n  retry_on: [FileNotFoundError]\n",
        })
        assert out["ok"] is False
        assert "supported" in out["error"]
        assert yaml_path.read_text(encoding="utf-8") == before
        assert not yaml_path.with_name("tools.yaml.bak").exists()


class TestPromptConstraint:
    def test_prompt_lists_supported_tools_yaml_fields(self):
        """诊断 prompt 应明确 tools.yaml 只支持的字段集（防 LLM 幻觉）。"""
        from orchestrator.coding.skill_diagnoser import PROMPT_TEMPLATE
        for field_name in ("name", "description", "parameters", "command",
                           "timeout_sec"):
            assert field_name in PROMPT_TEMPLATE

    def test_failed_event_error_visible_in_prompt(self, skills_dir, failed_result):
        """失败事件的 error 摘要应送入诊断 prompt（Phase 28 T1）。"""
        failed_result.tool_events[0]["error"] = "SCRIPT_ERROR: exit 2 boom"
        llm = Mock()
        llm.chat.return_value = _suggestion_json()
        diag = SkillDiagnoser(llm, skills_dir)
        out = diag.diagnose(failed_result, "任务")
        assert out["ok"] is True
        prompt = llm.chat.call_args.args[0][1].content   # 第二条 ChatMessage
        assert "SCRIPT_ERROR: exit 2 boom" in prompt

    def test_condense_events_without_error_backward_compatible(self):
        """旧事件无 error 键 → _condense_events 正常输出（向后兼容）。"""
        diag = SkillDiagnoser(Mock(), Path("."))
        text = diag._condense_events([{"step": 1, "name": "run_qc", "ok": False}])
        assert '"ok": false' in text and '"name": "run_qc"' in text


class TestPromptGrounding:
    """Phase 28 真机验收修复：归因纪律（A）+ tools.yaml 真实定义注入（B）。"""

    def test_prompt_requires_verbatim_error_quote(self, skills_dir, failed_result):
        """A：prompt 要求 issue 逐字引用 error、多失败事件逐个归因。"""
        for e in failed_result.tool_events:
            if not e["ok"]:
                e["error"] = "SCRIPT_ERROR: crc mismatch"
        llm = Mock()
        llm.chat.return_value = _suggestion_json()
        diag = SkillDiagnoser(llm, skills_dir)
        diag.diagnose(failed_result, "qc 任务")
        prompt = llm.chat.call_args.args[0][1].content
        assert "逐字引用" in prompt
        assert "逐个分别归因" in prompt

    def test_prompt_includes_tool_definitions(self, skills_dir, failed_result):
        """B：涉及工具的真实 parameters 注入 prompt，防 patch 参数名盲猜。"""
        (skills_dir / "bioqc" / "tools.yaml").write_text(
            "tools:\n"
            "  - name: run_qc\n"
            "    description: 对 h5ad 执行标准 QC\n"
            "    parameters:\n"
            "      type: object\n"
            "      properties:\n"
            "        input_path: {type: string}\n"
            "      required: [input_path]\n"
            "    command: [python, qc.py]\n"
            "    timeout_sec: 60\n",
            encoding="utf-8",
        )
        llm = Mock()
        llm.chat.return_value = _suggestion_json()
        diag = SkillDiagnoser(llm, skills_dir)
        diag.diagnose(failed_result, "qc 任务")
        prompt = llm.chat.call_args.args[0][1].content
        assert "input_path" in prompt          # 真实参数名可见
        assert "现有工具定义" in prompt

    def test_prompt_tool_defs_truncated(self, skills_dir, failed_result):
        """B：超长工具定义截断，防 prompt 爆炸。"""
        huge = "x" * 3000
        (skills_dir / "bioqc" / "tools.yaml").write_text(
            f"tools:\n"
            f"  - name: run_qc\n"
            f"    description: {huge}\n"
            f"    command: [python, qc.py]\n",
            encoding="utf-8",
        )
        llm = Mock()
        llm.chat.return_value = _suggestion_json()
        diag = SkillDiagnoser(llm, skills_dir)
        diag.diagnose(failed_result, "qc 任务")
        prompt = llm.chat.call_args.args[0][1].content
        assert len(prompt) < 5000
