"""bio_trajectory_pipeline skill：加载与工具 schema（Phase 60 建议③）。"""

from pathlib import Path

from orchestrator.coding.skill_loader import SkillLoader

PROJECT_SKILLS = Path(__file__).resolve().parents[2] / "skills"


class TestTrajectorySkill:
    def test_skill_loads_with_knowledge(self):
        """真实 skills/ 根扫描：新 skill 被加载且知识面含编排序列。"""
        loader = SkillLoader(PROJECT_SKILLS)
        loader.scan()
        skill = next((s for s in loader.skills if s.name == "bio_trajectory_pipeline"), None)
        assert skill is not None, "bio_trajectory_pipeline 未被 SkillLoader 加载"
        # 知识面必须携带锚定纪律与调用序列（防退化成空壳 skill）
        full = (skill.description or "") + (skill.body or "")
        for token in ("trajectory_full", "root_cluster", "list_cols"):
            assert token in full, f"知识面缺关键编排知识：{token}"

    def test_tool_schema_required_and_defaults(self):
        """tools.yaml：必填 dataset_id/root_cluster，steps 默认 fast。"""
        import yaml

        spec = yaml.safe_load(
            (PROJECT_SKILLS / "bio_trajectory_pipeline" / "tools.yaml").read_text(encoding="utf-8")
        )
        (tool,) = spec["tools"]
        assert tool["name"] == "bio_trajectory_pipeline"
        assert set(tool["parameters"]["required"]) == {"dataset_id", "root_cluster"}
        props = tool["parameters"]["properties"]
        assert props["steps"]["default"] == "fast"
        assert props["species"]["default"] == "human"
        assert tool["timeout_sec"] >= 7200
