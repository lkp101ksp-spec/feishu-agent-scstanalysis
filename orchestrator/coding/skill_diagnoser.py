"""skill 失败诊断：轨迹分析 → 改进建议 → 审批后写回（Phase 27 T1+T3）。

spec：/code 任务失败时由 LLM 分析 LoopResult 轨迹，生成 skill 改进建议；
人工审批通过后由 apply() 写回 skill 文件：
- SKILL.md  → 追加 "## 改进记录（date）" 段落
- tools.yaml → YAML load → 字段更新 → dump 写回
写回前统一备份原文件为 <文件名>.bak。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from orchestrator.coding.skill_loader import installed_dir_for
from shared.schemas import ChatMessage

if TYPE_CHECKING:
    from orchestrator.coding.agent_loop import LoopResult

logger = logging.getLogger(__name__)

MAX_EVENTS_IN_PROMPT = 30     # 送入 prompt 的工具事件上限（防 prompt 爆炸）
MAX_TOOL_DEF_CHARS = 800      # 单个工具定义注入 prompt 的字符上限（Phase 28 验收 B）
REQUIRED_FIELDS = ("skill", "issue", "fix", "file", "patch")
JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
# tools.yaml 工具条目受支持的字段（与 skill_loader 消费面一致）；
# 诊断 LLM 幻觉出的其他字段（如 max_retries）apply 时直接丢弃
TOOLS_YAML_FIELDS = ("name", "description", "parameters", "command", "timeout_sec")

PROMPT_TEMPLATE = """你是 skill 诊断专家。任务失败轨迹如下：
- 任务：{task_text}
- 终止原因：{status} / {abort_reason}
- 工具事件：{tool_events}
- 涉及 skill：{involved_skills}
- 现有工具定义（patch 中的参数名/字段名必须与此一致，不得虚构）：
{tool_defs}

请分析：
1. 哪个 skill/工具出问题（或无 skill 问题）
2. 具体问题（描述不清/参数缺失/命令错误/超时）——issue 必须逐字引用
   对应失败事件 error 的关键报错片段；若多个工具/步骤失败，逐个分别归因，
   禁止合并成单一笼统原因（例如把某一步的超时当成所有失败的原因）
3. 改进建议（改 SKILL.md 描述 / tools.yaml 参数 / 增加示例）
4. 若改 SKILL.md，patch 给出追加的 Markdown 段落；若改 tools.yaml，
   patch 给出 YAML 字段映射（如 "timeout_sec: 600" 或 "run_qc:\\n  timeout_sec: 600"）

重要约束：tools.yaml 工具只支持以下字段：name / description / parameters /
command / timeout_sec。patch 中不得出现其他字段（如 max_retries、retry_on、
param_variants 均不支持，写了也会被丢弃）；重试策略类建议请改写到 SKILL.md。
command 形态硬性约定（改 command 时必须遵守）：argv 字符串列表（禁字符串，
执行器按列表展开）；子进程工作目录=skill 目录，脚本写相对文件名；运行时
不做占位符替换（禁 {{path}} 类），parameters 以 --参数名 值 旗标追加，脚本
须 argparse 接收；本机模式无 /skill/ 路径（那是配 image 的容器挂载点）。

只以 JSON 返回：{{"skill": "...", "issue": "...", "fix": "...", "file": "SKILL.md|tools.yaml", "patch": "..."}}"""  # noqa: E501


class SkillDiagnoser:
    """skill 失败诊断：轨迹分析 → 改进建议 → 审批写回。"""

    def __init__(self, llm: Any, skills_dir: Path) -> None:
        """llm 为 LLMRouter（用其 chat 纯文本接口）；skills_dir 为 skills/ 根目录。"""
        self.llm = llm
        self.skills_dir = Path(skills_dir)

    # ------------------------------------------------------------------ #
    def diagnose(self, loop_result: LoopResult, task_text: str) -> dict[str, Any]:
        """分析失败轨迹，返回改进建议 dict；无 skill 涉及或解析失败返回 {"ok": False}。"""
        tool_map = self._skill_tool_map()
        skill_events = [e for e in (loop_result.tool_events or [])
                        if e.get("name") in tool_map]
        if not skill_events:
            return {"ok": False, "reason": "no skill involved"}

        involved = sorted({tool_map[e["name"]] for e in skill_events})
        prompt = PROMPT_TEMPLATE.format(
            task_text=task_text[:1000],
            status=loop_result.status,
            abort_reason=loop_result.abort_reason or "(none)",
            tool_events=self._condense_events(loop_result.tool_events),
            involved_skills=", ".join(involved),
            tool_defs=self._tool_defs_text(
                self._load_tool_defs(),
                sorted({e["name"] for e in skill_events}),
            ),
        )
        try:
            raw = self.llm.chat([
                ChatMessage(role="system", content="你是 skill 诊断专家，只输出 JSON。"),
                ChatMessage(role="user", content=prompt),
            ])
        except Exception as exc:  # noqa: BLE001 —— LLM 故障不应打断诊断流程
            logger.warning("diagnose llm call failed: %s", exc)
            return {"ok": False, "reason": f"llm error: {exc}"}

        suggestion = self._parse_json(raw)
        if suggestion is None:
            return {"ok": False, "reason": "parse error"}
        missing = [k for k in REQUIRED_FIELDS if k not in suggestion]
        if missing:
            return {"ok": False, "reason": f"missing fields: {missing}"}
        suggestion["ok"] = True
        return suggestion

    # ------------------------------------------------------------------ #
    def apply(self, suggestion: dict[str, Any]) -> dict[str, Any]:
        """审批通过后写回 skill 文件；失败返回错误 dict 而非抛异常。"""
        skill_name = str(suggestion.get("skill", "")).strip()
        file_kind = str(suggestion.get("file", "")).strip()
        patch = suggestion.get("patch")
        if not skill_name:
            return {"ok": False, "error": "missing skill name"}
        if patch is None or (isinstance(patch, str) and not patch.strip()):
            return {"ok": False, "error": "empty patch"}

        # 双目录解析（2026-09-23 挂账⑥）：安装目录优先（install 落点），
        # 回退内置目录（内置 skill 改进场景）
        skill_dir = installed_dir_for(self.skills_dir) / skill_name
        if not skill_dir.is_dir():
            skill_dir = self.skills_dir / skill_name
        if not skill_dir.is_dir():
            return {"ok": False, "error": f"skill dir not found: {skill_dir}"}

        if file_kind == "SKILL.md":
            return self._apply_skill_md(skill_dir / "SKILL.md", suggestion)
        if file_kind == "tools.yaml":
            return self._apply_tools_yaml(skill_dir / "tools.yaml", suggestion)
        return {"ok": False, "error": f"unsupported file kind: {file_kind}"}

    # ------------------------------------------------------------------ #
    def _apply_skill_md(self, md_path: Path, suggestion: dict[str, Any]) -> dict[str, Any]:
        """SKILL.md 写回：备份后追加 "## 改进记录（date）" 段落。"""
        if not md_path.is_file():
            return {"ok": False, "error": f"file not found: {md_path}"}
        backup = self._backup(md_path)
        section = (
            f"\n\n## 改进记录（{date.today().isoformat()}）\n\n"
            f"- 问题：{suggestion.get('issue', '')}\n"
            f"- 建议：{suggestion.get('fix', '')}\n\n"
            f"{str(suggestion['patch']).strip()}\n"
        )
        with md_path.open("a", encoding="utf-8") as fh:
            fh.write(section)
        logger.info("skill md patched: %s (backup %s)", md_path, backup)
        return {"ok": True, "file": str(md_path), "backup": str(backup)}

    def _apply_tools_yaml(self, yaml_path: Path, suggestion: dict[str, Any]) -> dict[str, Any]:
        """tools.yaml 写回：备份后按 patch 映射更新工具字段再 dump。"""
        if not yaml_path.is_file():
            return {"ok": False, "error": f"file not found: {yaml_path}"}
        try:
            patch_map = yaml.safe_load(str(suggestion["patch"]))
        except yaml.YAMLError as exc:
            return {"ok": False, "error": f"patch not valid YAML: {exc}"}
        if not isinstance(patch_map, dict) or not patch_map:
            return {"ok": False, "error": "patch must be a non-empty mapping"}

        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        tools = data.get("tools") or []
        if not tools:
            return {"ok": False, "error": "tools.yaml has no tools"}

        updated = self._merge_tool_patch(tools, patch_map)
        if not updated:
            return {"ok": False, "error":
                    "patch matched no supported tools.yaml fields "
                    f"(allowed: {', '.join(TOOLS_YAML_FIELDS)})"}

        backup = self._backup(yaml_path)
        yaml_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        logger.info("tools.yaml patched: %s (backup %s)", yaml_path, backup)
        return {"ok": True, "file": str(yaml_path), "backup": str(backup),
                "updated": updated}

    # ------------------------------------------------------------------ #
    def _merge_tool_patch(self, tools: list[dict[str, Any]], patch_map: dict[str, Any]) -> list[str]:
        """合并 patch（仅白名单字段）：{"工具名": {字段: 值}} 逐工具更新；{字段: 值} 作用于首个工具。

        schema 外字段（LLM 幻觉）在此被过滤丢弃，永不落盘。
        """

        def _sub_fields(sub: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in sub.items() if k in TOOLS_YAML_FIELDS}

        tool_names = {str(t.get("name")) for t in tools}
        updated: list[str] = []
        if any(k in tool_names for k in patch_map):
            for tool in tools:
                sub = patch_map.get(str(tool.get("name")))
                if isinstance(sub, dict):
                    for field_name, val in _sub_fields(sub).items():
                        tool[field_name] = val
                        updated.append(f"{tool.get('name')}.{field_name}")
        else:
            tool = tools[0]
            for field_name, val in _sub_fields(patch_map).items():
                tool[field_name] = val
                updated.append(f"{tool.get('name')}.{field_name}")
        return updated

    def _iter_tools_yamls(self) -> list[Path]:
        """内置 + 安装双目录的全部 tools.yaml 路径（2026-09-23 挂账⑥）。"""
        paths: list[Path] = []
        for base in (self.skills_dir, installed_dir_for(self.skills_dir)):
            if base.is_dir():
                paths.extend(sorted(base.glob("*/tools.yaml")))
        return paths

    def _skill_tool_map(self) -> dict[str, str]:
        """扫描双目录 */tools.yaml，构建 工具名 → skill 名 映射。"""
        mapping: dict[str, str] = {}
        for yaml_path in self._iter_tools_yamls():
            try:
                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            for tool in data.get("tools") or []:
                if isinstance(tool, dict) and tool.get("name"):
                    mapping[str(tool["name"])] = yaml_path.parent.name
        return mapping

    def _load_tool_defs(self) -> dict[str, dict[str, Any]]:
        """扫描双目录 */tools.yaml，构建 工具名 → 工具定义 dict 映射。"""
        defs: dict[str, dict[str, Any]] = {}
        for yaml_path in self._iter_tools_yamls():
            try:
                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            for tool in data.get("tools") or []:
                if isinstance(tool, dict) and tool.get("name"):
                    defs[str(tool["name"])] = tool
        return defs

    def _tool_defs_text(self, defs: dict[str, dict[str, Any]], names: list[str]) -> str:
        """把涉及工具的定义渲染为 YAML 文本，单工具截断防 prompt 爆炸。"""
        lines: list[str] = []
        for name in names:
            tool = defs.get(name)
            if tool is None:
                continue
            dumped = yaml.safe_dump(
                {name: tool}, allow_unicode=True, sort_keys=False)
            lines.append(dumped.strip()[:MAX_TOOL_DEF_CHARS])
        return "\n".join(lines) if lines else "(none)"

    def _condense_events(self, events: list[dict[str, Any]]) -> str:
        """压缩工具事件：优先保留失败事件，总量封顶，单行 JSON 输出。

        失败事件的 error 摘要（Phase 28 T1 起由 AgentLoop 附带）一并送入
        prompt，使诊断基于真实报错而非 ok 标志猜测。
        """
        events = events or []
        failed = [e for e in events if not e.get("ok")]
        kept = failed + [e for e in events if e.get("ok")]
        kept = kept[:MAX_EVENTS_IN_PROMPT]
        lines = []
        for e in kept:
            row = {"step": e.get("step"), "name": e.get("name"),
                   "ok": bool(e.get("ok"))}
            if not e.get("ok") and e.get("error"):
                row["error"] = str(e["error"])[:500]
            lines.append(json.dumps(row, ensure_ascii=False))
        return "\n".join(lines) if lines else "(none)"

    def _parse_json(self, raw: str) -> "dict[str, Any] | None":
        """解析 LLM 返回：剥离 ```json 围栏后提取首个 JSON 对象。"""
        if not raw:
            return None
        text = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "")
        match = JSON_BLOCK_RE.search(text)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None

    def _backup(self, path: Path) -> Path:
        """写回前备份原文件为 <文件名>.bak，返回备份路径。"""
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)
        return backup
