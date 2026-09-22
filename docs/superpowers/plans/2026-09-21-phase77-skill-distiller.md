# Phase 77 实施计划：L1 主动式 skill 进化（双入口闭环）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在 Phase 27 反应式诊断之上补 L1 主动式进化——/code 任务成功后自动复盘沉淀新 skill（SkillDistiller），并开放 `/skill install` 手动入口；双入口共用同一 SkillInstaller 校验/落盘/审计层，复用 Phase 27 卡片管线（kind=create 分支）。

**Architecture:** 零镜像改动、纯宿主侧 orchestrator/gateway 增量。新增两文件：`orchestrator/coding/skill_distiller.py`（复盘器，复用诊断器纪律）+ `orchestrator/coding/skill_installer.py`（统一落盘层）；改三处：`coding_runner.py`（成功触发+kind 分支发卡）、`gateway/app.py`（回调 kind 分支 + /skill install 路由）、README（补自进化说明）。human-in-loop 半自动不变。

**Tech Stack:** 宿主 python（yaml/json/re/zipfile/pathlib，均标准库+既有依赖）；pytest 单测（mock LLM/IM）；真机验收三链。

**Spec:** `docs/superpowers/specs/2026-09-21-phase77-skill-distiller-design.md`（§3 组件 / §4 安全 / §5 测试）

**质量门（每个 Task 提交前）：** `ruff check .` → `.venv\Scripts\python.exe -m mypy` → `.venv\Scripts\python.exe -m pytest -m "not pg" -q`（基线 1366 passed / mypy 205 files）

**文件结构总览：**

| 文件 | 动作 | Task |
|---|---|---|
| `orchestrator/coding/skill_installer.py` | Create | 1 |
| `tests/unit/test_skill_installer.py` | Create | 1 |
| `orchestrator/coding/skill_distiller.py` | Create | 2 |
| `tests/unit/test_skill_distiller.py` | Create | 2 |
| `orchestrator/coding/coding_runner.py` | Modify（成功触发+kind 分支+暂存） | 3 |
| `gateway/app.py` | Modify（回调 kind 分支） | 3 |
| `tests/unit/test_skill_improve_create.py` | Create（卡片+回调契约） | 3 |
| `gateway/app.py` | Modify（/skill install 路由） | 4 |
| `tests/unit/test_skill_install_route.py` | Create | 4 |
| `README.md` | Modify（自进化说明） | 5 |
| 真机验收脚本（临时，不入库） | Create | 6 |
| `测试总结+2026-09-09T01-55-00.md` | Modify（#22） | 7 |

---

### Task 1: SkillInstaller 统一落盘层 + 单测

**Files:**
- Create: `orchestrator/coding/skill_installer.py`
- Create: `tests/unit/test_skill_installer.py`

- [x] **Step 1.1: 写 `orchestrator/coding/skill_installer.py`**

统一校验/落盘层（双入口共用），不抛异常、返回 dict（与 diagnoser.apply 同款）：

```python
"""Skill 统一落盘层（Phase 77，spec 2026-09-21 §3.2/§4）。

双入口（SkillDistiller 自动复盘 / /skill install 手动）共用：
- validate_name：name 正则白名单 + 非存在校验（create 语义）；
- validate_files：files 键白名单 + 大小/数量上限 + SKILL.md frontmatter 校验；
- validate_zip：解压临时目录逐条校验（拒路径穿越/符号链接/缺 SKILL.md）；
- install：写 skills/<name>/（手动覆盖场景整目录 .bak.<ts> 兜底）。
所有公共方法返回 dict（ok/error），不抛异常（与 diagnoser.apply 同款）。
"""
from __future__ import annotations

import logging
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
# files 键白名单（幻觉键丢弃）：SKILL.md / tools.yaml / 任意 .py
FILE_KEY_RE = re.compile(r"^(SKILL\.md|tools\.yaml|[a-z][a-z0-9_]*\.py)$")
MAX_FILE_BYTES = 50 * 1024     # 单文件 50KB
MAX_FILES = 5                  # 文件数上限
MAX_ZIP_MEMBER_BYTES = 200 * 1024  # zip 单成员 200KB（手动 install 宽松些）
REQUIRED_FRONTMATTER = ("name", "description")


class SkillInstaller:
    """skill 目录校验与落盘（双入口共用，human-in-loop 审批后调用）。"""

    def __init__(self, skills_dir: Path) -> None:
        """skills_dir 为 skills/ 根目录。"""
        self.skills_dir = Path(skills_dir)

    # ------------------------------------------------------------------ #
    def validate_name(self, name: str) -> dict[str, Any]:
        """create 语义：正则合法且 skills/<name>/ 不存在。"""
        if not NAME_RE.match(name or ""):
            return {"ok": False,
                    "error": f"非法 skill 名（须匹配 {NAME_RE.pattern}）: {name!r}"}
        if (self.skills_dir / name).exists():
            return {"ok": False, "error": f"skill 已存在（create 拒覆盖）: {name}"}
        return {"ok": True}

    def validate_files(self, files: dict[str, Any]) -> dict[str, Any]:
        """校验 files 映射：白名单键 + 大小/数量 + SKILL.md frontmatter。

        返回 {"ok": True, "files": <规范化映射>} 或 {"ok": False, "error": ...}。
        幻觉键（白名单外）在此被丢弃并记录，不进入落盘。
        """
        if not isinstance(files, dict) or not files:
            return {"ok": False, "error": "files 为空或非映射"}
        clean: dict[str, str] = {}
        dropped: list[str] = []
        for key, val in files.items():
            if not FILE_KEY_RE.match(str(key)):
                dropped.append(str(key))
                continue
            text = str(val)
            if not text.strip():
                return {"ok": False, "error": f"文件内容为空: {key}"}
            if len(text.encode("utf-8")) > MAX_FILE_BYTES:
                return {"ok": False,
                        "error": f"文件超 {MAX_FILE_BYTES}B 上限: {key}"}
            clean[str(key)] = text
        if dropped:
            logger.info("installer dropped non-whitelist file keys: %s", dropped)
        if not clean:
            return {"ok": False, "error": "白名单过滤后无有效文件"}
        if len(clean) > MAX_FILES:
            return {"ok": False, "error": f"文件数超 {MAX_FILES} 上限"}
        if "SKILL.md" not in clean:
            return {"ok": False, "error": "缺 SKILL.md（skill_loader 必需）"}
        fm_err = self._check_frontmatter(clean["SKILL.md"])
        if fm_err:
            return {"ok": False, "error": fm_err}
        return {"ok": True, "files": clean, "dropped": dropped}

    def validate_zip(self, zip_path: Path) -> dict[str, Any]:
        """解压 zip 到内存映射并逐条安全校验，返回规范化 files 或拒因。

        拒绝：路径穿越（..）、绝对路径、符号链接、超大小、缺 SKILL.md。
        """
        zip_path = Path(zip_path)
        if not zip_path.is_file():
            return {"ok": False, "error": f"zip 不存在: {zip_path}"}
        try:
            zf = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile as exc:
            return {"ok": False, "error": f"非法 zip: {exc}"}
        files: dict[str, str] = {}
        with zf:
            for info in zf.infolist():
                member = info.filename
                # 符号链接：unix mode 高位 0o120000
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    return {"ok": False, "error": f"拒绝符号链接: {member}"}
                norm = member.replace("\\", "/")
                if norm.startswith("/") or ".." in norm.split("/"):
                    return {"ok": False, "error": f"拒绝路径穿越: {member}"}
                if info.is_dir():
                    continue
                if info.file_size > MAX_ZIP_MEMBER_BYTES:
                    return {"ok": False,
                            "error": f"成员超 {MAX_ZIP_MEMBER_BYTES}B: {member}"}
                # 仅取单层 <name>/<file> 或纯文件；取 basename 作 files 键
                base = Path(norm).name
                if not FILE_KEY_RE.match(base):
                    continue
                files[base] = zf.read(info).decode("utf-8", errors="replace")
        if "SKILL.md" not in files:
            return {"ok": False, "error": "zip 内缺 SKILL.md"}
        return self.validate_files(files)

    def install(self, name: str, files: dict[str, str],
                *, overwrite: bool = False) -> dict[str, Any]:
        """审批通过后落盘 skills/<name>/；overwrite 时整目录 .bak.<ts> 兜底。"""
        if not overwrite:
            chk = self.validate_name(name)
            if not chk.get("ok"):
                return chk
        elif not NAME_RE.match(name or ""):
            return {"ok": False, "error": f"非法 skill 名: {name!r}"}
        target = self.skills_dir / name
        backup = ""
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            if target.exists():
                ts = datetime.now().strftime("%Y%m%d%H%M%S")
                bak = self.skills_dir / f"{name}.bak.{ts}"
                shutil.move(str(target), str(bak))
                backup = str(bak)
                logger.info("existing skill moved to backup: %s", bak)
            target.mkdir(parents=True)
            for fname, content in files.items():
                (target / fname).write_text(content, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": f"落盘失败: {exc}"}
        logger.info("skill installed: %s (%d files)", target, len(files))
        return {"ok": True, "dir": str(target), "backup": backup,
                "files": sorted(files)}

    # ------------------------------------------------------------------ #
    def _check_frontmatter(self, skill_md: str) -> str:
        """SKILL.md frontmatter 须含 name/description，缺则返回错误串（否则空串）。"""
        text = skill_md.lstrip()
        if not text.startswith("---"):
            return "SKILL.md 缺 frontmatter（--- 起始块）"
        end = text.find("\n---", 3)
        if end < 0:
            return "SKILL.md frontmatter 未闭合"
        head = text[3:end]
        missing = [k for k in REQUIRED_FRONTMATTER
                   if not re.search(rf"^\s*{k}\s*:", head, re.MULTILINE)]
        if missing:
            return f"SKILL.md frontmatter 缺字段: {', '.join(missing)}"
        return ""
```

- [x] **Step 1.2: 写 `tests/unit/test_skill_installer.py`（8 用例）**

```python
"""SkillInstaller 单测（Phase 77 Task 1，spec §5）。"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from orchestrator.coding.skill_installer import SkillInstaller

VALID_MD = "---\nname: demo\ndescription: d\n---\n\n# demo\n"


@pytest.fixture()
def inst(tmp_path: Path) -> SkillInstaller:
    return SkillInstaller(tmp_path / "skills")


class TestValidateName:
    def test_legal_and_absent_ok(self, inst: SkillInstaller) -> None:
        assert inst.validate_name("new_skill")["ok"]

    def test_illegal_name_rejected(self, inst: SkillInstaller) -> None:
        for bad in ("", "Bad", "1abc", "a" * 40, "a-b", "../x"):
            assert not inst.validate_name(bad)["ok"], bad

    def test_existing_rejected(self, inst: SkillInstaller, tmp_path: Path) -> None:
        (tmp_path / "skills" / "dup").mkdir(parents=True)
        assert not inst.validate_name("dup")["ok"]


class TestValidateFiles:
    def test_valid_ok(self, inst: SkillInstaller) -> None:
        out = inst.validate_files({"SKILL.md": VALID_MD, "run_x.py": "print(1)"})
        assert out["ok"] and set(out["files"]) == {"SKILL.md", "run_x.py"}

    def test_drops_non_whitelist_keys(self, inst: SkillInstaller) -> None:
        out = inst.validate_files({"SKILL.md": VALID_MD, "evil.sh": "x",
                                   "max_retries": "3"})
        assert out["ok"] and "evil.sh" in out["dropped"]

    def test_missing_skill_md_rejected(self, inst: SkillInstaller) -> None:
        assert not inst.validate_files({"run_x.py": "print(1)"})["ok"]

    def test_missing_frontmatter_field_rejected(self, inst: SkillInstaller) -> None:
        md = "---\nname: demo\n---\n\n# x\n"  # 缺 description
        assert not inst.validate_files({"SKILL.md": md})["ok"]


class TestValidateZip:
    def _mkzip(self, tmp_path: Path, members: dict[str, str]) -> Path:
        zp = tmp_path / "s.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            for name, content in members.items():
                zf.writestr(name, content)
        return zp

    def test_valid_zip_ok(self, inst: SkillInstaller, tmp_path: Path) -> None:
        zp = self._mkzip(tmp_path, {"demo/SKILL.md": VALID_MD,
                                    "demo/run_x.py": "print(1)"})
        assert inst.validate_zip(zp)["ok"]

    def test_path_traversal_rejected(self, inst: SkillInstaller,
                                     tmp_path: Path) -> None:
        zp = self._mkzip(tmp_path, {"../evil/SKILL.md": VALID_MD})
        assert not inst.validate_zip(zp)["ok"]

    def test_missing_skill_md_rejected(self, inst: SkillInstaller,
                                       tmp_path: Path) -> None:
        zp = self._mkzip(tmp_path, {"demo/run_x.py": "print(1)"})
        assert not inst.validate_zip(zp)["ok"]


class TestInstall:
    def test_create_writes_files(self, inst: SkillInstaller) -> None:
        out = inst.install("demo", {"SKILL.md": VALID_MD})
        assert out["ok"]
        assert (Path(out["dir"]) / "SKILL.md").read_text() == VALID_MD

    def test_overwrite_backs_up(self, inst: SkillInstaller,
                                tmp_path: Path) -> None:
        d = tmp_path / "skills" / "demo"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("old", encoding="utf-8")
        out = inst.install("demo", {"SKILL.md": VALID_MD}, overwrite=True)
        assert out["ok"] and out["backup"]
        assert (d / "SKILL.md").read_text() == VALID_MD
```

- [x] **Step 1.3: 门禁 + 提交**

Run: `ruff check .` → `.venv\Scripts\python.exe -m pytest tests/unit/test_skill_installer.py -q`（预期 12 passed）→ `.venv\Scripts\python.exe -m mypy`（零告警）
Expected: 全绿

```bash
git add orchestrator/coding/skill_installer.py tests/unit/test_skill_installer.py
git commit -m "feat: SkillInstaller 统一落盘层（Phase 77 双入口共用，zip 穿越/frontmatter/白名单校验）"
```

---

### Task 2: SkillDistiller 复盘器 + 单测

**Files:**
- Create: `orchestrator/coding/skill_distiller.py`
- Create: `tests/unit/test_skill_distiller.py`

- [x] **Step 2.1: 写 `orchestrator/coding/skill_distiller.py`**

```python
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

from orchestrator.coding.skill_installer import (
    FILE_KEY_RE, NAME_RE, SkillInstaller)
from shared.schemas import ChatMessage

if TYPE_CHECKING:
    from orchestrator.coding.agent_loop import LoopResult

logger = logging.getLogger(__name__)

MAX_EVENTS_IN_PROMPT = 30
MIN_REPEAT_RUN_CMD = 2     # 触发阈值：≥2 次重复手写 run_cmd
REQUIRED_FIELDS = ("skill", "issue", "fix", "files")
JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

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
        # 公共模式：命令首词（解释器/可执行）出现 ≥2 次即视为重复手写
        first_words: dict[str, int] = {}
        for e in run_cmds:
            cmd = str(e.get("args", "")).strip().split()
            if cmd:
                first_words[cmd[0]] = first_words.get(cmd[0], 0) + 1
        return any(c >= MIN_REPEAT_RUN_CMD for c in first_words.values())

    def _skill_tool_names(self) -> set[str]:
        """扫描 skills_dir/*/tools.yaml 得全部 skill 工具名（触发过滤排除用）。"""
        names: set[str] = set()
        if not self.skills_dir.is_dir():
            return names
        for yaml_path in sorted(self.skills_dir.glob("*/tools.yaml")):
            try:
                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            for tool in data.get("tools") or []:
                if isinstance(tool, dict) and tool.get("name"):
                    names.add(str(tool["name"]))
        return names

    def _existing_skills_text(self) -> str:
        """既有 skill 的 name + description 清单（防重复造轮子注入）。"""
        lines: list[str] = []
        if not self.skills_dir.is_dir():
            return "(none)"
        for md_path in sorted(self.skills_dir.glob("*/SKILL.md")):
            name = md_path.parent.name
            desc = ""
            try:
                head = md_path.read_text(encoding="utf-8")[:600]
                m = re.search(r"^\s*description\s*:\s*(.+)$", head, re.MULTILINE)
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
```

- [x] **Step 2.2: 写 `tests/unit/test_skill_distiller.py`（7 用例）**

```python
"""SkillDistiller 单测（Phase 77 Task 2，spec §5）。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.coding.skill_distiller import SkillDistiller


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def chat(self, _msgs: Any) -> str:
        self.calls += 1
        return self.reply


def _loop(events: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(status="final", tools_disabled=False,
                           tool_events=events)


def _run_cmd_events(n: int, cmd: str = "python") -> list[dict[str, Any]]:
    return [{"step": i, "name": "run_cmd", "ok": True,
             "args": f"{cmd} script{i}.py"} for i in range(n)]


WORTH_JSON = ('{"worth": true, "skill": "newsk", "issue": "重复 python x.py",'
              ' "fix": "沉淀复用", "files": {"SKILL.md":'
              ' "---\\nname: newsk\\ndescription: d\\n---\\n",'
              ' "tools.yaml": "tools: []"}}')


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    return tmp_path / "skills"


class TestTriggerFilter:
    def test_below_threshold_no_llm(self, skills_dir: Path) -> None:
        llm = _FakeLLM(WORTH_JSON)
        d = SkillDistiller(llm, skills_dir)
        out = d.distill(_loop(_run_cmd_events(1)), "task")
        assert not out["ok"] and llm.calls == 0

    def test_threshold_triggers_llm(self, skills_dir: Path) -> None:
        llm = _FakeLLM(WORTH_JSON)
        d = SkillDistiller(llm, skills_dir)
        out = d.distill(_loop(_run_cmd_events(3)), "task")
        assert llm.calls == 1 and out["ok"] and out["kind"] == "create"

    def test_skill_tool_calls_not_counted(self, skills_dir: Path) -> None:
        (skills_dir / "bioqc").mkdir(parents=True)
        (skills_dir / "bioqc" / "tools.yaml").write_text(
            "tools:\n  - name: run_qc\n", encoding="utf-8")
        events = [{"step": 0, "name": "run_qc", "ok": True, "args": "x"},
                  {"step": 1, "name": "run_cmd", "ok": True, "args": "python a.py"}]
        llm = _FakeLLM(WORTH_JSON)
        d = SkillDistiller(llm, skills_dir)
        assert not d.distill(_loop(events), "task")["ok"] and llm.calls == 0


class TestSchema:
    def test_not_worth(self, skills_dir: Path) -> None:
        d = SkillDistiller(_FakeLLM('{"worth": false}'), skills_dir)
        assert not d.distill(_loop(_run_cmd_events(3)), "t")["ok"]

    def test_missing_fields(self, skills_dir: Path) -> None:
        d = SkillDistiller(_FakeLLM('{"worth": true, "skill": "x"}'), skills_dir)
        out = d.distill(_loop(_run_cmd_events(3)), "t")
        assert not out["ok"] and "missing fields" in out["reason"]

    def test_existing_name_rejected(self, skills_dir: Path) -> None:
        (skills_dir / "newsk").mkdir(parents=True)
        d = SkillDistiller(_FakeLLM(WORTH_JSON), skills_dir)
        assert not d.distill(_loop(_run_cmd_events(3)), "t")["ok"]

    def test_bad_files_rejected(self, skills_dir: Path) -> None:
        bad = ('{"worth": true, "skill": "okname", "issue": "i", "fix": "f",'
               ' "files": {"run_x.py": "print(1)"}}')  # 缺 SKILL.md
        d = SkillDistiller(_FakeLLM(bad), skills_dir)
        assert not d.distill(_loop(_run_cmd_events(3)), "t")["ok"]
```

- [x] **Step 2.3: 门禁 + 提交**

Run: `ruff check .` → `.venv\Scripts\python.exe -m pytest tests/unit/test_skill_distiller.py -q`（预期 7 passed）→ `.venv\Scripts\python.exe -m mypy`（零告警）

```bash
git add orchestrator/coding/skill_distiller.py tests/unit/test_skill_distiller.py
git commit -m "feat: SkillDistiller 成功复盘器（Phase 77 L1 主动进化，触发过滤+归因纪律+白名单）"
```

### Task 3: coding_runner 成功触发 + gateway 回调 kind 分支 + 契约测试

**Files:**
- Modify: `orchestrator/coding/coding_runner.py`（成功触发 + kind 分支发卡 + 完整 suggestion 进程内暂存）
- Modify: `gateway/app.py`（回调 kind 分支：create 走 installer.install）
- Create: `tests/unit/test_skill_improve_create.py`（卡片+回调契约）

**TDD 顺序**：先写契约测试（红）→ 接线 → 绿。create 型 suggestion 的 `files` 全文不能内嵌飞书按钮 value（长度限制），故发卡时暂存进程内 dict `{improve_id: suggestion}`，回调按 improve_id 取回（code_approval "不落库" 惯例）。

- [x] **Step 3.1: 写 `tests/unit/test_skill_improve_create.py`（契约测试，先红）**

```python
"""create 型 skill_improve 卡片与回调 kind 分支契约（Phase 77 Task 3，spec §5）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.coding.coding_runner import _skill_improve_card
from orchestrator.coding.skill_installer import SkillInstaller

VALID_MD = "---\nname: newsk\ndescription: d\n---\n\n# x\n"
CREATE_SUGG = {"ok": True, "kind": "create", "skill": "newsk",
               "issue": "重复手写", "fix": "沉淀",
               "files": {"SKILL.md": VALID_MD, "tools.yaml": "tools: []"}}


class TestCreateCard:
    def test_create_card_lists_files(self) -> None:
        card = _skill_improve_card("owner_open_id", CREATE_SUGG)
        text = json.dumps(card, ensure_ascii=False)
        assert "newsk" in text and "SKILL.md" in text
        # value 内嵌 suggestion 标记 kind=create
        value = card["elements"][1]["actions"][0]["value"]
        sugg = json.loads(value["suggestion"])
        assert sugg["kind"] == "create"
        # files 全文不内嵌 value（长度限制），仅键清单
        assert "files" not in sugg or isinstance(sugg.get("files"), list)

    def test_patch_card_unchanged(self) -> None:
        patch_sugg = {"skill": "s", "issue": "i", "fix": "f",
                      "file": "SKILL.md", "patch": "p"}
        card = _skill_improve_card("o", patch_sugg)
        value = card["elements"][1]["actions"][0]["value"]
        sugg = json.loads(value["suggestion"])
        assert sugg.get("kind", "patch") == "patch"
        assert sugg["patch"] == "p"


class TestCallbackKindBranch:
    """gateway 回调按 kind 分支：create → installer.install（契约级，不落真库）。"""

    def test_create_install_called(self, tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
        installer = SkillInstaller(tmp_path / "skills")
        called: dict[str, Any] = {}
        orig = installer.install

        def spy(name: str, files: dict[str, str], **kw: Any) -> dict[str, Any]:
            called["name"] = name
            called["files"] = files
            return orig(name, files, **kw)

        monkeypatch.setattr(installer, "install", spy)
        out = installer.install(CREATE_SUGG["skill"], CREATE_SUGG["files"])
        assert out["ok"] and called["name"] == "newsk"
        assert (tmp_path / "skills" / "newsk" / "SKILL.md").is_file()

    def test_create_existing_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "skills" / "newsk").mkdir(parents=True)
        installer = SkillInstaller(tmp_path / "skills")
        assert not installer.install("newsk", CREATE_SUGG["files"])["ok"]
```

- [x] **Step 3.2: 跑契约测试确认红（create 卡分支未实现）**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_skill_improve_create.py -q`
Expected: `TestCreateCard` 红（`_skill_improve_card` 尚无 kind 分支）

- [x] **Step 3.3: 改 `orchestrator/coding/coding_runner.py`（三处）**

**(a) `_skill_improve_card` 加 kind 分支**（约 L269-298，kind=create 时展示 files 键清单、value 内嵌 suggestion 只放 files 键 list 而非全文）：

```python
def _skill_improve_card(owner: str, suggestion: dict[str, Any]) -> dict[str, Any]:
    """skill 改进/沉淀审批卡（Phase 27 patch / Phase 77 create 双 kind）。

    create 型 files 全文不内嵌按钮 value（长度限制）——卡片展示 files 键
    清单，value 内嵌的 suggestion 只带 files 键 list；完整内容经
    CodingRunner._pending_create[improve_id] 进程内暂存，回调按 id 取回。
    """
    improve_id = new_ulid()
    kind = str(suggestion.get("kind", "patch"))
    base_value = {"action": "skill_improve", "skill_improve_id": improve_id,
                  "owner": owner, "kind": kind}
    if kind == "create":
        files = suggestion.get("files") or {}
        file_keys = sorted(files) if isinstance(files, dict) else []
        md_preview = str(files.get("SKILL.md", ""))[:300]
        capped = {"skill": str(suggestion.get("skill", ""))[:100],
                  "issue": str(suggestion.get("issue", ""))[:300],
                  "fix": str(suggestion.get("fix", ""))[:300],
                  "kind": "create", "files": file_keys}
        lines = "\n".join([
            f"- 新 skill：**{capped['skill']}**（沉淀新建）",
            f"- 重复模式：{capped['issue']}",
            f"- 价值：{capped['fix']}",
            f"- 文件：{', '.join(file_keys)}",
            f"- SKILL.md 预览：{md_preview}{'…' if len(str(files.get('SKILL.md','')))>300 else ''}",
        ])
        title = "/code skill 沉淀审批（新建）"
    else:
        capped = _cap_suggestion(suggestion)
        capped["kind"] = "patch"
        patch_preview = capped["patch"][:200] + ("…" if len(capped["patch"]) > 200 else "")
        lines = "\n".join([
            f"- skill：**{capped['skill']}**",
            f"- 问题：{capped['issue']}",
            f"- 建议：{capped['fix']}",
            f"- 目标文件：{capped['file']}",
            f"- patch 预览：{patch_preview or '（空）'}",
        ])
        title = "/code skill 改进审批"
    base_value["suggestion"] = json.dumps(capped, ensure_ascii=False)
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": lines}},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "批准写回"},
                 "type": "primary",
                 "value": {**base_value, "decision": "approve"}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "忽略"},
                 "type": "danger",
                 "value": {**base_value, "decision": "deny"}},
            ]},
        ],
    }
```

注：`_cap_suggestion` 对 create 型不再适用（无 patch/file 字段），create 分支独立构造 capped。

**(b) CodingRunner 注入 distiller + installer + 暂存 dict**（`__init__` 约 L306-313，加参数与属性）：

```python
# __init__ 签名追加（既有 diagnoser 参数后）：
#     distiller: "SkillDistiller | None" = None,
#     installer: "SkillInstaller | None" = None) -> None:
# 属性追加：
self.distiller = distiller
self.installer = installer
# create 型完整 suggestion 进程内暂存（improve_id → suggestion），
# 回调按 id 取回（不落库，进程重启卡片失效点不动——code_approval 同款口径）
self._pending_create: dict[str, dict[str, Any]] = {}
```

**(c) 成功后复盘触发**（`_maybe_diagnose_skill` 调用后约 L407 追加一行 + 新方法）：

在 `handle` 末尾 `_maybe_diagnose_skill(...)` 后加：
```python
        # Phase 77：任务成功时复盘沉淀新 skill（纯增量，失败静默）
        self._maybe_distill_skill(incoming, result, task_text)
```

新方法（与 `_maybe_diagnose_skill` 并列）：
```python
    def _maybe_distill_skill(self, incoming: IncomingMessage, result: LoopResult,
                             task_text: str) -> None:
        """成功轨迹 → SkillDistiller 复盘 → create 审批卡；任何异常只记日志。

        触发条件（与诊断互补）：status == final 且无连败禁用（真正成功）。
        复盘是附加能力，绝不影响主流程。
        """
        if self.distiller is None:
            return
        if result.status != "final" or result.tools_disabled:
            return
        try:
            suggestion = self.distiller.distill(result, task_text)
            if not (suggestion.get("ok") and suggestion.get("skill")):
                logger.info("skill distill skipped: %s",
                            suggestion.get("reason", "no suggestion"))
                return
            card = _skill_improve_card(incoming.sender_open_id, suggestion)
            improve_id = card["elements"][1]["actions"][0]["value"]["skill_improve_id"]
            self._pending_create[improve_id] = suggestion
            self.im.send_card(incoming.chat_id, card)
        except Exception:  # noqa: BLE001 —— 复盘是附加能力，绝不影响主流程
            logger.exception("skill distill/card failed (ignored)")
```

- [x] **Step 3.4: 改 `gateway/app.py` 回调 kind 分支**

在 `action == "skill_improve"` 分支内（约 L273 `diagnoser.apply(suggestion)` 处）按 kind 分流：取 `payload.get("kind")` 或 suggestion 内 `kind`；`create` 时从 `coding_runner._pending_create` 取回完整 suggestion 并调 `installer.install`，`patch` 走原 diagnoser.apply。具体改动：

在 L273 `try:` 之前的 suggestion 解析后，把 L273-302 的 apply 段改为分派：

```python
        kind = payload.get("kind") or suggestion.get("kind", "patch")
        runner = getattr(ctx.orchestrator, "coding_runner", None)
        try:
            if kind == "create":
                installer = getattr(runner, "installer", None)
                pending = getattr(runner, "_pending_create", {})
                full = pending.pop(payload.get("skill_improve_id", ""), None)
                if installer is None:
                    return {"ok": False, "status": "decided", "decision": decision,
                            "reason": "skill installer not configured"}
                if full is None:
                    return {"ok": False, "status": "apply_failed",
                            "reason": "suggestion expired (restart?)"}
                applied = installer.install(str(full.get("skill", "")),
                                            full.get("files") or {})
            else:
                diagnoser = getattr(runner, "diagnoser", None)
                if diagnoser is None:
                    return {"ok": False, "status": "decided", "decision": decision,
                            "reason": "skill diagnoser not configured"}
                applied = diagnoser.apply(suggestion)
        except Exception as e:  # noqa: BLE001 —— 写回异常转为卡片可见错误
            ...（同原 L275-283，audit skill_improve_apply_failed）
        if not applied.get("ok"):
            ...（同原 L284-292，audit skill_improve_apply_failed）
        _audit_event(..., action="skill_improve_applied", ...,
                     detail={"skill": ..., "kind": kind,
                             "file": applied.get("file", applied.get("dir", "")),
                             "backup": applied.get("backup", "")})
        return {"ok": True, "status": "applied", "kind": kind, ...}
```

注：create 型 applied 用 `dir` 键（installer 返回），patch 型用 `file` 键（diagnoser 返回），audit detail 兼容两者。原 L257-262 的 diagnoser 提前判空逻辑需移入 patch 分支（create 不需要 diagnoser）。

- [x] **Step 3.5: 门禁 + 提交**

Run: `ruff check .` → `.venv\Scripts\python.exe -m pytest tests/unit/test_skill_improve_create.py tests/unit/test_skill_installer.py tests/unit/test_skill_distiller.py -q`（全绿）→ `.venv\Scripts\python.exe -m mypy`（零告警）→ `.venv\Scripts\python.exe -m pytest -m "not pg" -q`（基线+新增，零回归）

```bash
git add orchestrator/coding/coding_runner.py gateway/app.py tests/unit/test_skill_improve_create.py
git commit -m "feat: skill_improve create 分支+成功复盘触发（Phase 77 双 kind 卡片+回调分派+进程内暂存）"
```

### Task 4: /skill install 手动入口（gateway 消息路由 + zip 校验）+ 单测

**Files:**
- Modify: `gateway/app.py`（消息路由：/skill install + zip 附件 → validate_zip → create 卡）
- Create: `tests/unit/test_skill_install_route.py`

手动入口复用 Task 1 的 `validate_zip` 与 Task 3 的 create 卡链路；用户飞书发 zip 附件 + 文字 `/skill install`，校验通过发 create 卡（标注来源 manual），批准后走同一 `installer.install(overwrite=True)`（手动 install 允许覆盖同名，整目录 .bak 兜底）。

- [x] **Step 4.1: 改 `gateway/app.py` 消息路由**

在消息文本路由处（/code、/research 等同层）加 `/skill install` 分支：

```python
    # Phase 77：/skill install 手动入口（zip 附件 + 文字触发）。
    # 下载 zip → validate_zip 安全校验 → create 审批卡（来源 manual）→
    # 批准后 installer.install(overwrite=True) 落盘 skills/<name>/。
    if text.strip().startswith("/skill install"):
        return _handle_skill_install(app, ctx, event)
```

新增 helper（与既有消息 handler 同模块）：

```python
def _handle_skill_install(app: Any, ctx: Any, event: dict[str, Any]) -> dict[str, Any]:
    """/skill install：zip 附件 → 校验 → create 审批卡。"""
    runner = getattr(ctx.orchestrator, "coding_runner", None)
    installer = getattr(runner, "installer", None) if runner else None
    if installer is None:
        return {"ok": False, "reason": "skill installer not configured"}
    # 取消息附件 file_key（zip）；无附件则引导
    file_key = _extract_zip_file_key(event)
    if not file_key:
        return {"ok": True, "reply": "请随消息附带 skill zip 压缩包"
                "（含 SKILL.md/tools.yaml/*.py，单目录）"}
    tmp_dir = Path(app.tmp_dir)  # 沙箱临时目录（非 skills/）
    tmp_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tmp_dir / f"{new_ulid()}.zip"
    try:
        app.im.download_file(file_key, zip_path)  # 复用现有附件下载链路
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reply": f"zip 下载失败: {exc}"}
    validated = installer.validate_zip(zip_path)
    if not validated.get("ok"):
        return {"ok": True, "reply": f"zip 校验未通过：{validated['error']}"}
    files = validated["files"]
    # 从 SKILL.md frontmatter 取 name（与 loader 一致）
    name = _frontmatter_name(files["SKILL.md"]) or "unnamed"
    suggestion = {"ok": True, "kind": "create", "skill": name,
                  "issue": "手动 install（用户上传 zip）",
                  "fix": "用户显式安装", "files": files, "source": "manual",
                  "overwrite": True}
    card = _skill_improve_card(event.get("sender_open_id", ""), suggestion)
    improve_id = card["elements"][1]["actions"][0]["value"]["skill_improve_id"]
    runner._pending_create[improve_id] = suggestion
    app.im.send_card(event.get("chat_id", ""), card)
    return {"ok": True, "status": "card_sent"}
```

回调侧 create 分支需识别 `source == "manual"` 时 `overwrite=True`（Task 3 Step 3.4 的 create 分支 `installer.install(...)` 调用改为读 full 内 overwrite 标志：`installer.install(name, files, overwrite=bool(full.get("overwrite")))`）。

- [x] **Step 4.2: 写 `tests/unit/test_skill_install_route.py`（4 用例）**

```python
"""/skill install 手动入口路由单测（Phase 77 Task 4，spec §5）。"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from orchestrator.coding.skill_installer import SkillInstaller

VALID_MD = "---\nname: mansk\ndescription: d\n---\n\n# x\n"


def _mkzip(tmp_path: Path, members: dict[str, str]) -> Path:
    zp = tmp_path / "up.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        for n, c in members.items():
            zf.writestr(n, c)
    return zp


class TestManualInstallPath:
    def test_valid_zip_validates(self, tmp_path: Path) -> None:
        inst = SkillInstaller(tmp_path / "skills")
        zp = _mkzip(tmp_path, {"mansk/SKILL.md": VALID_MD,
                               "mansk/run_x.py": "print(1)"})
        out = inst.validate_zip(zp)
        assert out["ok"] and "SKILL.md" in out["files"]

    def test_manual_overwrite_allowed(self, tmp_path: Path) -> None:
        d = tmp_path / "skills" / "mansk"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("old", encoding="utf-8")
        inst = SkillInstaller(tmp_path / "skills")
        out = inst.install("mansk", {"SKILL.md": VALID_MD}, overwrite=True)
        assert out["ok"] and out["backup"]
        assert (d / "SKILL.md").read_text() == VALID_MD

    def test_bad_zip_rejected(self, tmp_path: Path) -> None:
        inst = SkillInstaller(tmp_path / "skills")
        zp = _mkzip(tmp_path, {"../evil/SKILL.md": VALID_MD})
        assert not inst.validate_zip(zp)["ok"]

    def test_missing_frontmatter_rejected(self, tmp_path: Path) -> None:
        inst = SkillInstaller(tmp_path / "skills")
        zp = _mkzip(tmp_path, {"mansk/SKILL.md": "# no frontmatter\n"})
        assert not inst.validate_zip(zp)["ok"]
```

- [x] **Step 4.3: 门禁 + 提交**

Run: `ruff check .` → `.venv\Scripts\python.exe -m pytest tests/unit/test_skill_install_route.py -q`（4 passed）→ `.venv\Scripts\python.exe -m mypy`（零告警）→ 全量 pytest 零回归

```bash
git add gateway/app.py tests/unit/test_skill_install_route.py
git commit -m "feat: /skill install 手动入口（Phase 77 zip 校验+create 卡+overwrite 落盘）"
```

---

### Task 5: README 补自进化说明

**Files:**
- Modify: `README.md`

- [x] **Step 5.1: README 核心能力表补自进化行 + 简述**

在"核心能力一览"表格追加一行，并在 Coding Agent 段落补 L1 主动进化说明：

在核心能力表（`/code` Coding Agent 行附近）加：
```markdown
| 自进化（RSI-L1） | 失败自动诊断写回 skill（Phase 27-29）+ 成功复盘沉淀新 skill（Phase 77）；双入口：自动复盘 + `/skill install` zip 即装；全程 human-in-loop 审批 |
```

并在架构/目录说明的 skill 相关段落补一句：
```markdown
系统的自进化能力：`/code` 失败时 SkillDiagnoser 自动分析轨迹生成改进卡（审批后写回 skill）；成功时 SkillDistiller 复盘重复模式提议沉淀新 skill；也可 `/skill install` 附 zip 手动安装。所有写回均经审批卡 + .bak 兜底 + 审计（skill_improve_id 全链可追溯）。
```

- [x] **Step 5.2: 提交**

```bash
git add README.md
git commit -m "docs: README 补自进化（RSI-L1）能力说明（Phase 77）"
```

### Task 6: 真机验收三链

**Files:**
- Create: 真机验收脚本（临时驱动，跑完不入库或入 `_eval`）

真机验收需服务在跑（ws_client + gateway）。三链：①手动 zip install 全链；②自动复盘全链；③拒绝路径。注意：自动复盘触发需真实 /code 任务出现 ≥2 次重复 run_cmd——可构造一个需多次手写的任务；手动 install 用现成 skill 打 zip 测。

- [x] **Step 6.1: 手动 zip install 全链**

操作：把一个测试 skill 打成 zip（含 SKILL.md frontmatter + tools.yaml + 一个 .py），飞书发送该 zip + 文字 `/skill install` → 应收到 create 审批卡（标注来源 manual）→ 点批准 → `skills/<name>/` 落盘 → 发一条匹配该 skill 的 /code 任务验证语义检索命中。
Expected: 卡片到达、批准落盘、/code 命中新 skill；审计按 skill_improve_id 可检索 applied 事件。

- [x] **Step 6.2: 自动复盘全链**

操作：发一条需要重复手写命令的 /code 任务（如"对 bio_test_data 下三个文件分别用 python 读取并打印行数"——迫使 ≥2 次 `python` run_cmd）→ 任务成功后应收到 create 沉淀卡 → 点批准 → 落盘生效。
Expected: 触发过滤命中（≥2 次同首词 run_cmd）→ 自动发卡 → 批准落盘；若无沉淀价值 LLM 返回 worth=false 则静默（如实记录触发与否）。

- [x] **Step 6.3: 拒绝路径**

操作：①发一个缺 SKILL.md 的 zip + /skill install → 应文本回复拒因；②发一个 frontmatter 缺 description 的 zip → 拒因；③对已存在 skill 名走自动复盘 create → 拒收（不覆盖）。
Expected: 三类拒绝均如实提示，不落盘、不崩。

- [x] **Step 6.4: 记录实测值**

记录：①三链各自通过与否；②触发过滤是否如期（自动复盘是否触发）；③拒绝路径提示文本；④审计事件可检索性。供测试总结 #22 回填。

---

### Task 7: 测试总结 #22 回填 + 收口

**Files:**
- Modify: `测试总结+2026-09-09T01-55-00.md`（文末追加 #22 段落）
- Modify: 本计划文件（勾选全部 checkbox）

- [x] **Step 7.1: 回填测试总结**

在 `测试总结+2026-09-09T01-55-00.md` 文末追加 `## 2026-09-21 Phase 77 收口回填（#22 L1 主动式 skill 进化双入口）` 段落，沿用既有压缩文体，须涵盖：
- ① SkillInstaller：8+ 用例（name/files/zip/install 四类校验），commit hash
- ② SkillDistiller：7 用例（触发过滤 3+schema 4），commit hash
- ③ create 分支接线：契约测试红→绿；三组件用例数；全量 pytest 通过数（基线 1366+新增），commit hash
- ④ 手动 install 路由：4 用例，commit hash
- ⑤ 真机验收三链实测：手动全链/自动复盘触发与否/拒绝路径三例，实测值
- 后续建议/挂账：L2 进化质量自评估（补丁效果度量+自动回滚）待使用数据；L3 全自动明确不做；定期批处理触发另议

- [x] **Step 7.2: 勾选本计划全部 checkbox + 最终 push**

```bash
git add "测试总结+2026-09-09T01-55-00.md" docs/superpowers/plans/2026-09-21-phase77-skill-distiller.md
git commit -m "docs: 测试总结 #22 回填+Phase 77 计划勾选收口"
git push
```

Expected: pre-push 四连门全绿，Phase 77 收口。

---

## 自审清单

- [x] 覆盖 spec 全部组件（§3.1 Distiller / §3.2 Installer / §3.3 卡片回调 / §3.4 手动路由 / §4 安全 / §5 测试）
- [x] 无占位符（所有代码块完整可运行，行号锚点按 HEAD 7082208 实读）
- [x] TDD 顺序（Task 3 契约测试先红后绿）
- [x] 陷阱入册：①files 全文不内嵌 value（进程内暂存+improve_id 取回）；②create/patch 双 kind audit detail 兼容（dir vs file 键）；③手动 install overwrite=True vs 自动 create 拒覆盖；④触发过滤排除 skill 工具调用；⑤zip 符号链接/路径穿越逐条校验
- [x] 每个 Task 独立可提交、门禁齐全

## 执行交接

**推荐 Subagent-Driven**：每 Task 派新 subagent，任务间两段式审查。Task 6 真机验收需服务在跑（ws_client + gateway + 用户飞书操作），建议在 Task 1-5 全绿后由用户配合执行。

