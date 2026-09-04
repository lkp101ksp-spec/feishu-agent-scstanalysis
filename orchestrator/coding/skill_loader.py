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
    return {t.lower() for t in re.findall(r"[A-Za-z0-9_]+|[一-鿿]", text)}


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
