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


# === Phase 29 T1：容器隔离（可选 image 字段） ===


class TestContainerIsolation:
    def _register_with_image(self, skills_dir, monkeypatch):
        """写入带 image 的工具并注册；mock skill_loader.subprocess.run。"""
        (skills_dir / "bioqc" / "tools.yaml").write_text(
            "tools:\n"
            "  - name: run_qc\n"
            "    description: 对 h5ad 执行标准 QC\n"
            f"    command: [{Path(sys.executable).as_posix()}, -c, \"print('qc done')\"]\n"
            "    image: bio:cpu-latest\n"
            "    timeout_sec: 60\n",
            encoding="utf-8",
        )
        loader = SkillLoader(skills_dir)
        loader.scan()
        reg = ToolRegistry()
        loader.register_tools(reg)
        import orchestrator.coding.skill_loader as sl
        calls = []
        proc = type("P", (), {"returncode": 0, "stdout": "qc done", "stderr": ""})()
        def fake_run(argv, **kw):
            calls.append((argv, kw))
            return proc
        monkeypatch.setattr(sl.subprocess, "run", fake_run)
        return reg.get("run_qc").handler, calls

    def test_image_tool_runs_in_docker(self, skills_dir, monkeypatch):
        """配 image → docker run 隔离（network none + 资源限额 + skill 目录 ro）。"""
        handler, calls = self._register_with_image(skills_dir, monkeypatch)
        out = handler(input_path="data.h5ad")
        assert out.error_code == ""
        argv = calls[0][0]
        assert argv[0] == "docker" and argv[1] == "run"
        assert "--network" in argv and "none" in argv
        assert "--cpus" in argv and "--memory" in argv
        mount = argv[argv.index("-v") + 1]
        assert mount.endswith(":/skill:ro")
        assert argv[argv.index("-w") + 1] == "/skill"
        assert "bio:cpu-latest" in argv
        assert "--input_path" in argv and "data.h5ad" in argv  # 参数照常展开

    def test_image_tool_nonzero_stderr_reported(self, skills_dir, monkeypatch):
        """容器非零退出 → SCRIPT_ERROR + stderr 尾部。"""
        (skills_dir / "bioqc" / "tools.yaml").write_text(
            "tools:\n"
            "  - name: run_qc\n"
            "    description: QC\n"
            f"    command: [{Path(sys.executable).as_posix()}, -c, 1/0]\n"
            "    image: bio:cpu-latest\n",
            encoding="utf-8",
        )
        loader = SkillLoader(skills_dir)
        loader.scan()
        reg = ToolRegistry()
        loader.register_tools(reg)
        import orchestrator.coding.skill_loader as sl
        proc = type("P", (), {"returncode": 2, "stdout": "",
                              "stderr": "crc mismatch boom"})()
        monkeypatch.setattr(sl.subprocess, "run",
                            lambda argv, **kw: proc)
        out = reg.get("run_qc").handler(input_path="x")
        assert out.error_code == "SCRIPT_ERROR"
        assert "crc mismatch boom" in out.error_message

    def test_no_image_tool_stays_local(self, skills_dir, monkeypatch):
        """未配 image → 本机直跑（argv[0] 为命令本体而非 docker）。"""
        loader = SkillLoader(skills_dir)
        loader.scan()
        reg = ToolRegistry()
        loader.register_tools(reg)
        import orchestrator.coding.skill_loader as sl
        calls = []
        proc = type("P", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
        monkeypatch.setattr(sl.subprocess, "run",
                            lambda argv, **kw: (calls.append(argv), proc)[1])
        out = reg.get("run_qc").handler(input_path="data.h5ad")
        assert out.error_code == ""
        assert calls[0][0] == Path(sys.executable).as_posix()


# === Phase 29 T2：语义检索（LLM 选择 + 词元 fallback） ===


def _llm(return_value=None, raise_exc=None):
    from unittest.mock import Mock
    m = Mock()
    if raise_exc:
        m.chat.side_effect = raise_exc
    else:
        m.chat.return_value = return_value
    return m


class TestSemanticSelection:
    def test_llm_selects_skills(self, skills_dir):
        """LLM 选中 → 知识块按选中顺序、只含选中 skill。"""
        (skills_dir / "other" / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
        (skills_dir / "other" / "SKILL.md").write_text(
            "---\nname: other\ndescription: 无关工具\n---\n正文。\n", encoding="utf-8")
        loader = SkillLoader(skills_dir)
        loader.scan()
        llm = _llm(return_value='{"skills": ["bioqc"]}')
        text = loader.build_system_knowledge("quality control for cells", llm=llm)
        assert "bioqc" in text and "other" not in text

    def test_llm_hallucinated_names_filtered_to_fallback(self, skills_dir):
        """LLM 全幻觉 name → 过滤后空 → fallback 词元法。"""
        loader = SkillLoader(skills_dir)
        loader.scan()
        llm = _llm(return_value='{"skills": ["ghost_skill"]}')
        text = loader.build_system_knowledge("帮我做单细胞质控 qc", llm=llm)
        assert "bioqc" in text          # fallback 命中

    def test_llm_error_falls_back(self, skills_dir):
        """LLM 异常 → 词元法，不抛。"""
        loader = SkillLoader(skills_dir)
        loader.scan()
        llm = _llm(raise_exc=RuntimeError("llm down"))
        text = loader.build_system_knowledge("帮我做单细胞质控 qc", llm=llm)
        assert "bioqc" in text

    def test_llm_bad_json_falls_back(self, skills_dir):
        """LLM 返回非 JSON → 词元法。"""
        loader = SkillLoader(skills_dir)
        loader.scan()
        llm = _llm(return_value="我觉得都相关")
        text = loader.build_system_knowledge("单细胞质控", llm=llm)
        assert "bioqc" in text

    def test_no_skills_no_llm_call(self, tmp_path):
        """无 skill → 空串且不调 LLM。"""
        d = tmp_path / "skills"
        d.mkdir()
        loader = SkillLoader(d)
        loader.scan()
        llm = _llm(return_value='{"skills": ["x"]}')
        assert loader.build_system_knowledge("任务", llm=llm) == ""
        llm.chat.assert_not_called()
