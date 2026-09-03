# Phase 26 T4：SkillLoader（SKILL.md + tools.yaml）

> 前置阅读：`docs/superpowers/plans/2026-09-03-phase26-plan-overview.md`（共享约定）
> 背景：spec §3——skill = 知识面（SKILL.md）+ 工具面（可选 tools.yaml）。目录约定 `skills/<skill_name>/`，热加载（每次 /code 会话 scan 一次即可拾取新增）。知识面匹配 v1 用词元交集。
>
> SKILL.md 格式（frontmatter + Markdown 正文）：
> ```markdown
> ---
> name: bioqc
> description: 单细胞质控工具集（过滤低质量细胞/双细胞）
> ---
> 使用说明正文……（进入 system 提示的知识面）
> ```
>
> tools.yaml 格式（`tools` 列表，每项 name/description/parameters(JSON Schema)/command/timeout_sec 可选）：
> ```yaml
> tools:
>   - name: run_qc
>     description: 对 h5ad 执行标准 QC
>     parameters:
>       type: object
>       properties:
>         input_path: {type: string}
>       required: [input_path]
>     command: [python, run_qc.py]
>     timeout_sec: 600
> ```

**Files:**
- Create: `orchestrator/coding/skill_loader.py`
- Test: `tests/unit/test_coding_skill_loader.py`
- 依赖安装：pyyaml（清华源）

**接口契约**（与总览一致）：
- `SkillLoader(skills_dir: Path)`
- `.scan() -> int`（返回加载的 skill 数；缺 name/description 的跳过并 warn）
- `.register_tools(registry: ToolRegistry) -> int`（返回注册的工具数；重名跳过并 warn）
- `.build_system_knowledge(task_text: str) -> str`（词元交集 top-3 知识块；无命中返回 ""）

**已确认的现有接口**（勿改）：
- `ToolSpec(name, description, parameters, risk_level, handler, requires_approval=False, timeout_sec=60, ...)`——pydantic BaseModel，`RiskLevel = Literal["L0_read", "L1_compute", "L2_side_effect"]`（`orchestrator/tools/tool_registry.py` L14）
- `registry.register(spec)` / `registry.get(name)`（未注册 raise `ToolNotFoundError`）
- `ToolResult(outputs, artifacts_ids, error_code, error_message, blocks)`（`orchestrator/tools/tool_handler.py` L19）
- skill 工具注册为 `risk_level="L2_side_effect"`、`requires_approval=False`——**审批由 AgentLoop 的 risk_map + approve_fn 统一把关**（见 T5/T7），ToolHandler 层不重复审批

- [ ] **Step 1: 安装依赖**

Run: `.\.venv\Scripts\python.exe -m pip install pyyaml -i https://pypi.tuna.tsinghua.edu.cn/simple`

- [ ] **Step 2: 写失败测试**

```python
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
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_coding_skill_loader.py -v --basetemp=.pytest_basetemp`
Expected: collection FAIL（`ModuleNotFoundError: orchestrator.coding.skill_loader`）

- [ ] **Step 4: 实现**

创建 `orchestrator/coding/skill_loader.py`：

```python
"""skill 系统加载器：SKILL.md 知识面 + tools.yaml 工具面（Phase 26）。

spec 2026-09-03-phase26-code-agent-design §3：
- skills/<name>/SKILL.md（frontmatter 必填 name/description + Markdown 正文）
- skills/<name>/tools.yaml 可选：工具以子进程在 skill 目录内执行
- 工具统一注册为 L2（AgentLoop 层审批），知识面词元交集 v1 匹配
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from orchestrator.tools.tool_handler import ToolResult
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec

logger = logging.getLogger(__name__)


@dataclass
class LoadedSkill:
    """一个已加载的 skill：元信息 + 知识面正文 + 工具定义。"""
    name: str
    description: str
    body: str
    directory: Path
    tools: list[dict] = field(default_factory=list)


def _tokenize(text: str) -> set[str]:
    """v1 词元化：英文/数字整词 + 中文字符逐字（小写）。"""
    return {t.lower() for t in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text)}


def _make_handler(command: list[str], cwd: Path, timeout_sec: int) -> Callable:
    """把 skill 工具包装成 registry handler：子进程执行，参数 --k v 展开。"""

    def handler(**inputs) -> ToolResult:
        argv = list(command)
        for key, val in inputs.items():
            if val is True:                      # 布尔 → 开关旗标
                argv.append(f"--{key}")
            elif isinstance(val, list):          # 列表 → 逐项展开
                argv.extend(str(x) for x in val)
            else:
                argv.extend([f"--{key}", str(val)])
        try:
            proc = subprocess.run(
                argv, cwd=str(cwd), capture_output=True, text=True,
                timeout=timeout_sec, shell=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return ToolResult(outputs={}, artifacts_ids=[], error_code="SCRIPT_ERROR",
                              error_message=str(exc)[:2000], blocks=[])
        if proc.returncode != 0:
            return ToolResult(outputs={}, artifacts_ids=[], error_code="SCRIPT_ERROR",
                              error_message=proc.stderr[-2000:], blocks=[])
        return ToolResult(outputs={"stdout": proc.stdout[:4000]}, artifacts_ids=[],
                          error_code="", error_message="", blocks=[])

    return handler


class SkillLoader:
    """skills/ 目录扫描 + 工具注册 + 知识面匹配。"""

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = Path(skills_dir)
        self.skills: list[LoadedSkill] = []

    # ------------------------------------------------------------------ #
    def scan(self) -> int:
        """扫描 skills/<name>/SKILL.md 并（重）加载；残缺项跳过并 warn。"""
        self.skills = []
        if not self.skills_dir.is_dir():
            return 0
        for md in sorted(self.skills_dir.glob("*/SKILL.md")):
            skill = self._parse_skill(md)
            if skill is None:
                continue
            self.skills.append(skill)
        return len(self.skills)

    def _parse_skill(self, md_path: Path) -> "LoadedSkill | None":
        """解析单个 SKILL.md（frontmatter + 正文）与同目录 tools.yaml。"""
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("skill md unreadable: %s (%s)", md_path, exc)
            return None
        meta: dict = {}
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                meta = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()
        name = str(meta.get("name", "")).strip()
        description = str(meta.get("description", "")).strip()
        if not name or not description:
            logger.warning("skill skipped (missing name/description): %s", md_path.parent)
            return None
        tools = self._parse_tools(md_path.parent / "tools.yaml")
        return LoadedSkill(name=name, description=description, body=body,
                           directory=md_path.parent, tools=tools)

    def _parse_tools(self, yaml_path: Path) -> list[dict]:
        """解析 tools.yaml；文件缺失或格式错返回空表。"""
        if not yaml_path.is_file():
            return []
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.warning("tools.yaml invalid: %s (%s)", yaml_path, exc)
            return []
        tools = data.get("tools") or []
        return [t for t in tools if isinstance(t, dict) and t.get("name") and t.get("command")]

    # ------------------------------------------------------------------ #
    def register_tools(self, registry: ToolRegistry) -> int:
        """把所有 skill 工具注册进 registry（L2，重名跳过）；返回注册数。"""
        count = 0
        for skill in self.skills:
            for t in skill.tools:
                name = str(t["name"])
                try:
                    registry.get(name)
                    logger.warning("skill tool name conflict skipped: %s", name)
                    continue
                except Exception:  # ToolNotFoundError —— 未注册，正常路径
                    pass
                spec = ToolSpec(
                    name=name,
                    description=f"[skill:{skill.name}] {t.get('description', '')}",
                    parameters=t.get("parameters") or {"type": "object", "properties": {}},
                    risk_level="L2_side_effect",
                    handler=_make_handler(list(t["command"]), skill.directory,
                                          int(t.get("timeout_sec", 300))),
                    requires_approval=False,   # 审批由 AgentLoop risk_map 统一把关
                    timeout_sec=int(t.get("timeout_sec", 300)),
                )
                registry.register(spec)
                count += 1
        return count

    # ------------------------------------------------------------------ #
    def build_system_knowledge(self, task_text: str, top: int = 3) -> str:
        """知识面匹配 v1：任务词元 ∩ skill 词元打分，拼 top-N 提示块。"""
        toks = _tokenize(task_text)
        if not toks:
            return ""
        scored: list[tuple[int, LoadedSkill]] = []
        for s in self.skills:
            hay = _tokenize(f"{s.name} {s.description} {s.body}")
            score = len(toks & hay)
            if score > 0:
                scored.append((score, s))
        if not scored:
            return ""
        scored.sort(key=lambda x: (-x[0], x[1].name))
        blocks = [f"## 可用 skill：{s.name}\n{s.description}\n{s.body[:1500]}"
                  for _, s in scored[:top]]
        return "\n\n".join(blocks)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_coding_skill_loader.py -v --basetemp=.pytest_basetemp`
Expected: 全 PASS（8 用例）

- [ ] **Step 6: commit**

```bash
git add orchestrator/coding/skill_loader.py tests/unit/test_coding_skill_loader.py
git commit -m "feat(phase26): SkillLoader 解析 SKILL.md/tools.yaml 并注册 L2 工具"
```

（若 `requirements.txt`/`pyproject.toml` 存在依赖清单，同步补 `pyyaml` 条目后一并提交。）

完成后删除 `.pytest_basetemp`，回总览勾选 T4。
