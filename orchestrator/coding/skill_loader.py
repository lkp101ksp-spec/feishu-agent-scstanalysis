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
from typing import Any, Callable

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
    tools: list[dict[str, Any]] = field(default_factory=list)


def _tokenize(text: str) -> set[str]:
    """v1 词元化：英文/数字整词 + 中文字符逐字（小写）。"""
    return {t.lower() for t in re.findall(r"[A-Za-z0-9_]+|[一-鿿]", text)}


def installed_dir_for(skills_dir: Path) -> Path:
    """第三方/自动提炼 skill 的安装目录：skills_dir 同级 skills_installed/。

    2026-09-23 挂账⑥：与内置 skills/ 物理分离——mypy packages/ruff 门禁
    只管内置目录，安装进来的 LLM 产物（质量不可控）不再触发项目门禁；
    loader/diagnoser/distiller 双目录可见，installer 只写安装目录。
    """
    return Path(skills_dir).parent / "skills_installed"


def _make_handler(command: list[str], cwd: Path, timeout_sec: int,
                  image: str | None = None) -> Callable[..., ToolResult]:
    """把 skill 工具包装成 registry handler。

    本机模式：子进程执行（cwd=skill 目录），参数 --k v 展开。
    容器模式（Phase 29 T1，tools.yaml 配 image）：docker run --rm 隔离——
    网络禁用 + 资源限额 + skill 目录只读挂载 /skill；需写宿主路径的
    skill 不适用容器模式（不配 image 即可）。
    """

    def handler(**inputs: Any) -> ToolResult:
        argv = list(command)
        for key, val in inputs.items():
            if val is True:                      # 布尔 → 开关旗标
                argv.append(f"--{key}")
            elif isinstance(val, list):          # 列表 → 逐项展开
                argv.extend(str(x) for x in val)
            else:
                argv.extend([f"--{key}", str(val)])
        if image:
            host_dir = str(Path(cwd).resolve())
            argv = ["docker", "run", "--rm", "-i",
                    "--network", "none",
                    "--cpus", "2", "--memory", "4g",
                    "-v", f"{host_dir}:/skill:ro", "-w", "/skill",
                    image] + argv
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
        """扫描内置 skills/ + 安装 skills_installed/ 的 <name>/SKILL.md 并加载。

        残缺项跳过并 warn；同名冲突内置优先（安装目录项跳过告警，防遮蔽）。
        """
        self.skills = []
        seen: set[str] = set()
        for base in (self.skills_dir, installed_dir_for(self.skills_dir)):
            if not base.is_dir():
                continue
            for md in sorted(base.glob("*/SKILL.md")):
                # 防御性跳过隐藏/备份目录（.bak 已移出 skills/，兜历史残留）
                if md.parent.name.startswith(".") or ".bak" in md.parent.name:
                    continue
                skill = self._parse_skill(md)
                if skill is None:
                    continue
                if skill.name in seen:
                    logger.warning("skill name clash (builtin wins), skipped: %s",
                                   md.parent)
                    continue
                seen.add(skill.name)
                self.skills.append(skill)
        return len(self.skills)

    def _parse_skill(self, md_path: Path) -> "LoadedSkill | None":
        """解析单个 SKILL.md（frontmatter + 正文）与同目录 tools.yaml。"""
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("skill md unreadable: %s (%s)", md_path, exc)
            return None
        meta: dict[str, Any] = {}
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

    def _parse_tools(self, yaml_path: Path) -> list[dict[str, Any]]:
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
                registry.register(self._tool_spec(skill, t))
                count += 1
        return count

    def reload_skill(self, registry: ToolRegistry, name: str) -> dict[str, Any]:
        """热刷新单个 skill 的工具注册（安装/写回后免重启生效）。

        2026-09-22 挂账②：register_tools 重名跳过使已注册 skill 的
        tools.yaml 变更在运行期不生效（需重启）。此处按 [skill:<name>]
        描述前缀卸载旧工具（含新版已删除的工具），重扫该目录并强制
        重注册（register 同名覆写）。返回 {"ok", "removed", "added"}。
        """
        prefix = f"[skill:{name}]"
        removed = [t.name for t in registry.list()
                   if t.description.startswith(prefix)]
        for tn in removed:
            registry.unregister(tn)
        added: list[str] = []
        # 双目录解析（挂账⑥）：安装目录优先（install/patch 的落点），
        # 找不到再回退内置目录（内置 skill 的 diagnoser patch 场景）
        md = installed_dir_for(self.skills_dir) / name / "SKILL.md"
        if not md.is_file():
            md = self.skills_dir / name / "SKILL.md"
        skill = self._parse_skill(md)
        if skill is not None:
            for t in skill.tools:
                registry.register(self._tool_spec(skill, t))
                added.append(str(t["name"]))
        logger.info("skill hot reload: %s removed=%s added=%s", name, removed, added)
        return {"ok": True, "removed": removed, "added": added}

    @staticmethod
    def _tool_spec(skill: LoadedSkill, t: dict[str, Any]) -> ToolSpec:
        """由 tools.yaml 条目构造 ToolSpec（register_tools/reload_skill 共用）。"""
        return ToolSpec(
            name=str(t["name"]),
            description=f"[skill:{skill.name}] {t.get('description', '')}",
            parameters=t.get("parameters") or {"type": "object", "properties": {}},
            risk_level="L2_side_effect",
            handler=_make_handler(list(t["command"]), skill.directory,
                                  int(t.get("timeout_sec", 300)),
                                  image=t.get("image")),
            requires_approval=False,   # 审批由 AgentLoop risk_map 统一把关
            timeout_sec=int(t.get("timeout_sec", 300)),
        )

    # ------------------------------------------------------------------ #
    def build_system_knowledge(self, task_text: str, top: int = 3,
                               llm: Any = None) -> str:
        """知识面注入：llm 提供时语义选择（Phase 29 T2），否则/失败回退词元 v1。

        语义选择一次纯文本 LLM 调用（候选清单 → JSON name 列表），
        幻觉 name 过滤、全空/异常/解析失败一律回退词元法，绝不抛出。
        """
        if not self.skills:
            return ""
        names: list[str] = []
        if llm is not None:
            names = self._select_semantic(task_text, top, llm)
        if not names:
            ranked = self._rank_by_tokens(task_text)
            if not ranked:
                return ""
            names = [s.name for _, s in ranked[:top]]
        by_name = {s.name: s for s in self.skills}
        blocks = [f"## 可用 skill：{n}\n{by_name[n].description}\n"
                  f"{by_name[n].body[:1500]}"
                  for n in names if n in by_name]
        return "\n\n".join(blocks)

    def _rank_by_tokens(self, task_text: str) -> list[tuple[int, LoadedSkill]]:
        """词元交集打分 v1（语义选择的 fallback）。"""
        toks = _tokenize(task_text)
        if not toks:
            return []
        scored: list[tuple[int, LoadedSkill]] = []
        for s in self.skills:
            hay = _tokenize(f"{s.name} {s.description} {s.body}")
            score = len(toks & hay)
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        return scored

    def _select_semantic(self, task_text: str, top: int,
                         llm: Any) -> list[str]:
        """LLM 语义选择：候选清单 → 相关 name 列表（已过滤幻觉）。"""
        import json as _json
        import re as _re

        from shared.schemas import ChatMessage

        candidates = "\n".join(f"- {s.name}: {s.description}"
                               for s in self.skills)
        prompt = (
            "从候选 skill 列表中选出与任务相关的 skill（最多 "
            f"{top} 个，按相关度排序）。\n\n任务：{task_text[:500]}\n\n"
            f"候选 skill：\n{candidates}\n\n"
            '只返回 JSON：{"skills": ["name1", ...]}；name 必须来自候选'
            '列表，不得虚构；都不相关返回 {"skills": []}'
        )
        try:
            raw = llm.chat([ChatMessage(role="user", content=prompt)])
        except Exception as exc:  # noqa: BLE001 —— 检索失败回退词元法
            logger.warning("semantic skill select failed: %s", exc)
            return []
        text = _re.sub(r"```(?:json)?\s*", "", str(raw or "")).replace("```", "")
        match = _re.search(r"\{.*\}", text, _re.DOTALL)
        if not match:
            return []
        try:
            obj = _json.loads(match.group(0))
        except ValueError:
            return []
        valid = {s.name for s in self.skills}
        picked = obj.get("skills") if isinstance(obj, dict) else None
        if not isinstance(picked, list):
            return []
        return [str(n) for n in picked if str(n) in valid][:top]
