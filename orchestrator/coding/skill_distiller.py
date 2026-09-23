"""Skill 成功复盘（Phase 77，spec 2026-09-21 §3.1）：L1 主动式进化。

/code 任务成功后，若轨迹中出现"重复手写命令"（≥2 次非 skill 工具的
run_cmd 且有公共模式），调 LLM 判断是否值得沉淀为新 skill，产出 create
型 suggestion（files 多文件映射）。复用 SkillDiagnoser 的归因纪律与
幻觉字段白名单教训（Phase 28 / 875b280）。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from orchestrator.coding.skill_installer import FILE_KEY_RE, NAME_RE, SkillInstaller
from orchestrator.coding.skill_loader import installed_dir_for
from shared.schemas import ChatMessage

if TYPE_CHECKING:
    from orchestrator.coding.agent_loop import LoopResult

logger = logging.getLogger(__name__)

MAX_EVENTS_IN_PROMPT = 30
MIN_REPEAT_RUN_CMD = 2     # 触发阈值：≥2 次重复手写 run_cmd
REQUIRED_FIELDS = ("skill", "issue", "fix", "files")
JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
# 真机事件 args 为 {"cmd": [...]} JSON 摘要（agent_loop 100 字符截断，
# 可能截成非法 JSON）——用正则取 cmd 数组首元素，容忍截断
CMD_FIRST_RE = re.compile(r'"cmd"\s*:\s*\[\s*"([^"]+)"')

PROMPT_TEMPLATE = """你是 skill 沉淀专家。一个 /code 任务已成功完成，轨迹如下：
- 任务：{task_text}
- 工具事件：{tool_events}
- 既有 skill（避免重复造轮子，name + description）：
{existing_skills}

轨迹中出现了重复的手写命令模式。请判断：是否值得把这套重复操作沉淀为
一个可复用 skill？若值得，产出一个完整 skill 草案：
1. skill：新 skill 名（[a-z][a-z0-9_] 小写下划线，2-30 字符，不得与既有重复）
2. issue：逐字引用轨迹中重复的命令片段（禁笼统描述），说明重复模式
3. fix：沉淀价值（什么场景复用、省什么力气）
4. files：完整文件映射，键只允许 SKILL.md / tools.yaml / *.py：
   - SKILL.md 必须含 frontmatter（--- 块内 name: 与 description:）
   - tools.yaml 工具只支持字段 name/description/parameters/command/timeout_sec
   - *.py 为可被 command 调用的脚本

tools.yaml 硬性约定（违反即装上即坏，2026-09-22 真机实锤）：
   - command 必须是 argv 字符串列表，如 ["python", "run.py"]——禁止字符串
     （执行器按列表展开，字符串会被拆成单字符导致 WinError 2）
   - 子进程工作目录 = skill 目录，脚本用相对文件名即可
   - 运行时不做任何占位符替换（禁 {{{{path}}}} 之类）；parameters 会以
     --参数名 值 的旗标形式追加到 argv，脚本必须用 argparse 接收同名参数
   - 本机模式没有 /skill/ 路径（那是配 image 的容器模式挂载点）；要处理
     宿主文件时由调用方传入绝对路径参数

若不值得沉淀（一次性操作/与既有 skill 重叠/过于任务特异），只返回
{{"worth": false}}。

只以 JSON 返回：{{"worth": true, "skill": "...", "issue": "...", "fix": "...", "files": {{"SKILL.md": "...", "tools.yaml": "..."}}}}"""  # noqa: E501


class SkillDistiller:
    """任务成功后的 skill 复盘：触发过滤 → LLM 判断 → create 型 suggestion。"""

    def __init__(self, llm: Any, skills_dir: Path) -> None:
        """llm 为 LLMRouter（chat 纯文本接口）；skills_dir 为 skills/ 根目录。"""
        self.llm = llm
        self.skills_dir = Path(skills_dir)
        self.installer = SkillInstaller(skills_dir)

    # ------------------------------------------------------------------ #
    def distill(self, loop_result: LoopResult, task_text: str) -> dict[str, Any]:
        """成功轨迹复盘；不触发/无沉淀价值/校验失败返回 {"ok": False}。"""
        if not self._should_trigger(loop_result):
            return {"ok": False, "reason": "below trigger threshold"}
        prompt = PROMPT_TEMPLATE.format(
            task_text=task_text[:1000],
            tool_events=self._condense_events(loop_result.tool_events),
            existing_skills=self._existing_skills_text(),
        )
        try:
            raw = self.llm.chat([
                ChatMessage(role="system",
                            content="你是 skill 沉淀专家，只输出 JSON。"),
                ChatMessage(role="user", content=prompt),
            ])
        except Exception as exc:  # noqa: BLE001 —— LLM 故障不打断主流程
            logger.warning("distill llm call failed: %s", exc)
            return {"ok": False, "reason": f"llm error: {exc}"}

        obj = self._parse_json(raw)
        if obj is None:
            return {"ok": False, "reason": "parse error"}
        if not obj.get("worth", True):
            return {"ok": False, "reason": "llm judged not worth"}
        missing = [k for k in REQUIRED_FIELDS if k not in obj]
        if missing:
            return {"ok": False, "reason": f"missing fields: {missing}"}

        name = str(obj.get("skill", "")).strip()
        chk = self.installer.validate_name(name)
        if not chk.get("ok"):
            return {"ok": False, "reason": chk["error"]}
        vfiles = self.installer.validate_files(obj.get("files") or {})
        if not vfiles.get("ok"):
            return {"ok": False, "reason": vfiles["error"]}
        return {"ok": True, "kind": "create", "skill": name,
                "issue": str(obj["issue"]), "fix": str(obj["fix"]),
                "files": vfiles["files"]}

    # ------------------------------------------------------------------ #
    def _should_trigger(self, loop_result: LoopResult) -> bool:
        """规则前置过滤（不进 LLM）：≥2 次非 skill 工具的 run_cmd 重复调用。"""
        events = loop_result.tool_events or []
        skill_tools = self._skill_tool_names()
        run_cmds = [e for e in events
                    if e.get("name") == "run_cmd"
                    and e.get("name") not in skill_tools]
        if len(run_cmds) < MIN_REPEAT_RUN_CMD:
            return False
        # 公共模式：命令首词（解释器/可执行）出现 ≥2 次即视为重复手写。
        # args 双形态：真机为 {"cmd": [...]} JSON 摘要（正则提取，容忍截断），
        # 单测/旧轨迹为裸命令字符串（split 取首词）
        first_words: dict[str, int] = {}
        for e in run_cmds:
            raw = str(e.get("args", "")).strip()
            m = CMD_FIRST_RE.search(raw)
            if m:
                head = m.group(1)
            else:
                parts = raw.split()
                head = parts[0] if parts else ""
            if head:
                first_words[head] = first_words.get(head, 0) + 1
        return any(c >= MIN_REPEAT_RUN_CMD for c in first_words.values())

    def _skill_dirs(self) -> list[Path]:
        """内置 + 安装双目录中存在的根目录（2026-09-23 挂账⑥）。"""
        return [b for b in (self.skills_dir, installed_dir_for(self.skills_dir))
                if b.is_dir()]

    def _skill_tool_names(self) -> set[str]:
        """扫描双目录 */tools.yaml 得全部 skill 工具名（触发过滤排除用）。"""
        names: set[str] = set()
        for base in self._skill_dirs():
            for yaml_path in sorted(base.glob("*/tools.yaml")):
                try:
                    data = yaml.safe_load(
                        yaml_path.read_text(encoding="utf-8")) or {}
                except yaml.YAMLError:
                    continue
                for tool in data.get("tools") or []:
                    if isinstance(tool, dict) and tool.get("name"):
                        names.add(str(tool["name"]))
        return names

    def _existing_skills_text(self) -> str:
        """既有 skill 的 name + description 清单（防重复造轮子注入）。"""
        lines: list[str] = []
        for base in self._skill_dirs():
            for md_path in sorted(base.glob("*/SKILL.md")):
                name = md_path.parent.name
                desc = ""
                try:
                    head = md_path.read_text(encoding="utf-8")[:600]
                    m = re.search(r"^\s*description\s*:\s*(.+)$", head,
                                  re.MULTILINE)
                    desc = m.group(1).strip()[:120] if m else ""
                except OSError:
                    pass
                lines.append(f"- {name}: {desc}")
        return "\n".join(lines) if lines else "(none)"

    def _condense_events(self, events: list[dict[str, Any]]) -> str:
        """压缩工具事件（成功轨迹全保留，封顶），单行 JSON。"""
        kept = (events or [])[:MAX_EVENTS_IN_PROMPT]
        lines = []
        for e in kept:
            row = {"step": e.get("step"), "name": e.get("name"),
                   "ok": bool(e.get("ok"))}
            args = str(e.get("args", ""))
            if args:
                row["args"] = args[:200]
            lines.append(json.dumps(row, ensure_ascii=False))
        return "\n".join(lines) if lines else "(none)"

    def _parse_json(self, raw: str) -> "dict[str, Any] | None":
        """剥离 ```json 围栏后提取首个 JSON 对象。"""
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

    # 供测试/调试暴露的白名单常量（防 mypy 未用告警，文档化用途）
    _WHITELIST = (NAME_RE, FILE_KEY_RE)
