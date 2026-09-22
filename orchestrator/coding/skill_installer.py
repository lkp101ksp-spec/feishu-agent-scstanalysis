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

import yaml

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
        if "tools.yaml" in clean:
            ty_err = self._check_tools_yaml(clean["tools.yaml"])
            if ty_err:
                return {"ok": False, "error": ty_err}
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
        """审批通过后落盘 skills/<name>/；overwrite 时旧目录移 .skill_backups/ 兜底。

        备份目录放 skills/ 之外（skills_dir 同级 .skill_backups/）——2026-09-22
        挂账③：.bak 目录落在 skills/ 内会被 SkillLoader `*/SKILL.md` 扫描
        （重名工具跳过告警刷屏）且被 ruff/mypy 门禁扫到（旧脚本必挂 pre-push）。
        """
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
                bak_root = self.skills_dir.parent / ".skill_backups"
                bak_root.mkdir(parents=True, exist_ok=True)
                bak = bak_root / f"{name}.bak.{ts}"
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
    def _check_tools_yaml(self, tools_yaml: str) -> str:
        """tools.yaml 形态校验（2026-09-22 实锤驱动：LLM 产物字符串 command 装上即坏）。

        执行器（skill_loader._make_handler）按 `list(command)` 展开 argv——
        字符串会被拆成单字符（真机 WinError 2 根因），故 command 必须是
        非空字符串列表；占位符（{path}/{{files}}）不做替换，参数一律以
        --k v 旗标追加，脚本须自行 argparse 接收。
        """
        try:
            data = yaml.safe_load(tools_yaml) or {}
        except yaml.YAMLError as exc:
            return f"tools.yaml 非法 YAML: {exc}"
        tools = data.get("tools") or []
        if not isinstance(tools, list):
            return "tools.yaml 的 tools 字段须为列表"
        for t in tools:
            if not isinstance(t, dict) or not t.get("name"):
                return "tools.yaml 存在缺 name 的工具条目"
            cmd = t.get("command")
            if isinstance(cmd, str):
                return (f"工具 {t['name']} 的 command 是字符串——执行器按 argv "
                        "列表展开，字符串会被拆成单字符；请改为列表，"
                        '如 ["python", "run.py"]')
            if (not isinstance(cmd, list) or not cmd
                    or not all(isinstance(x, str) and x.strip() for x in cmd)):
                return f"工具 {t.get('name')} 的 command 须为非空字符串列表"
            if any(re.search(r"\{+\w+\}+", x) for x in cmd):
                return (f"工具 {t['name']} 的 command 含占位符（如 {{path}}）——"
                        "运行时不做替换，参数以 --k v 旗标追加，脚本须 argparse 接收")
        return ""

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
