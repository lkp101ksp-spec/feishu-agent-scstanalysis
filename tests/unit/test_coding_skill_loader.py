"""SkillLoader：SKILL.md/tools.yaml 解析、注册、知识面词元匹配（Phase 26 T4）。"""
import sys
from pathlib import Path

import pytest

from orchestrator.coding.skill_loader import SkillLoader
from orchestrator.tools.tool_registry import ToolRegistry


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    """构造含一个完整 skill + 一个残缺 skill 的目录。"""
    d = tmp_path / "skills"
    good = d / "bioqc"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text(
        "---\nname: bioqc\ndescription: 单细胞质控 qc 工具集\n---\n"
        "对 h5ad 数据做质控：过滤低质量细胞与双细胞。\n",
        encoding="utf-8",
    )
    (good / "tools.yaml").write_text(
        "tools:\n"
        "  - name: run_qc\n"
        "    description: 对 h5ad 执行标准 QC\n"
        "    parameters:\n"
        "      type: object\n"
        "      properties:\n"
        "        input_path: {type: string}\n"
        "      required: [input_path]\n"
        f"    command: [{Path(sys.executable).as_posix()}, -c, \"print('qc done')\"]\n"
        "    timeout_sec: 60\n",
        encoding="utf-8",
    )
    bad = d / "broken"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: broken\n---\n缺 description\n", encoding="utf-8")
    return d


class TestScan:
    def test_scan_loads_valid_skill_only(self, skills_dir):
        loader = SkillLoader(skills_dir)
        assert loader.scan() == 1
        assert loader.skills[0].name == "bioqc"

    def test_scan_is_reentrant_hot_reload(self, skills_dir):
        """热加载：二次 scan 拾取新增 skill。"""
        loader = SkillLoader(skills_dir)
        assert loader.scan() == 1
        (skills_dir / "extra" / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
        (skills_dir / "extra" / "SKILL.md").write_text(
            "---\nname: extra\ndescription: 备用\n---\n内容\n", encoding="utf-8")
        assert loader.scan() == 2

    def test_scan_missing_dir_returns_zero(self, tmp_path):
        assert SkillLoader(tmp_path / "nope").scan() == 0


class TestRegisterTools:
    def test_register_and_execute(self, skills_dir):
        loader = SkillLoader(skills_dir)
        loader.scan()
        reg = ToolRegistry()
        assert loader.register_tools(reg) == 1
        spec = reg.get("run_qc")
        assert spec.risk_level == "L2_side_effect"
        result = spec.handler(input_path="data.h5ad")
        assert "qc done" in str(result.outputs)

    def test_name_conflict_skipped(self, skills_dir):
        loader = SkillLoader(skills_dir)
        loader.scan()
        reg = ToolRegistry()
        from orchestrator.tools.tool_registry import ToolSpec
        reg.register(ToolSpec(
            name="run_qc", description="既有工具", parameters={"type": "object", "properties": {}},
            risk_level="L1_compute", handler=lambda **kw: None,
        ))
        assert loader.register_tools(reg) == 0  # 重名跳过


class TestKnowledge:
    def test_token_match_hits_skill(self, skills_dir):
        loader = SkillLoader(skills_dir)
        loader.scan()
        text = loader.build_system_knowledge("帮我做单细胞质控 qc")
        assert "bioqc" in text

    def test_no_match_returns_empty(self, skills_dir):
        loader = SkillLoader(skills_dir)
        loader.scan()
        assert loader.build_system_knowledge("写一个网页爬虫") == ""
