"""coding 域六原语工具集 + 统一 dispatch（Phase 26）。

spec 2026-09-03-phase26-code-agent-design §4：
- 文件五原语全部经 WorkspaceManager.resolve_safe 限定在会话目录内
- run_cmd 经 CommandPolicy 三态：allow 直跑 / need_approval 走 approve_fn / block 拒
- dispatch 永不抛异常，统一返回 {"ok": bool, ...}，由 AgentLoop 序列化为观察
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from orchestrator.coding.workspace import CommandPolicy, PathEscapeError, WorkspaceManager

logger = logging.getLogger(__name__)

# 单条工具输出截断阈值（字符），防止单条观察撑爆上下文
OBS_LIMIT = 4000


class CodeTools:
    """code 域六原语；dispatch 为 AgentLoop 的统一工具入口。"""

    # OpenAI function calling schema（六原语静态声明）
    SCHEMA = [
        {"type": "function", "function": {
            "name": "read_file",
            "description": "读取工作区内文本文件；offset/limit 为 1-based 行号窗口",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "会话内相对路径"},
                "offset": {"type": "integer", "description": "起始行（含），1-based"},
                "limit": {"type": "integer", "description": "读取行数"},
            }, "required": ["path"]},
        }},
        {"type": "function", "function": {
            "name": "write_file",
            "description": "整文件覆写（UTF-8）；自动创建父目录",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            }, "required": ["path", "content"]},
        }},
        {"type": "function", "function": {
            "name": "edit_file",
            "description": "精确替换：old_str 在文件中必须恰好出现一次",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "old_str": {"type": "string", "description": "待替换原文（须唯一）"},
                "new_str": {"type": "string", "description": "替换后文本，空串即删除"},
            }, "required": ["path", "old_str", "new_str"]},
        }},
        {"type": "function", "function": {
            "name": "list_dir",
            "description": "列出工作区内目录项（名称/类型/大小）",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "默认 '.'"},
            }},
        }},
        {"type": "function", "function": {
            "name": "search_files",
            "description": "grep 式搜索：递归扫描文件行，正则 pattern + glob 过滤",
            "parameters": {"type": "object", "properties": {
                "pattern": {"type": "string", "description": "Python 正则"},
                "path": {"type": "string", "description": "搜索起始目录，默认 '.'"},
                "glob": {"type": "string", "description": "文件名通配，默认 '*'"},
            }, "required": ["pattern"]},
        }},
        {"type": "function", "function": {
            "name": "run_cmd",
            "description": "在会话工作区执行命令（参数数组）；非白名单命令需人工审批",
            "parameters": {"type": "object", "properties": {
                "cmd": {"type": "array", "items": {"type": "string"}},
            }, "required": ["cmd"]},
        }},
    ]

    def __init__(
        self,
        ws: WorkspaceManager,
        session_id: str,
        *,
        approve_fn: Optional[Callable[[list[str]], bool]] = None,
        cmd_timeout_sec: int = 120,
    ) -> None:
        """绑定会话工作区；approve_fn 为 need_approval 命令的人工审批回调。"""
        self.ws = ws
        self.session_id = session_id
        self.approve_fn = approve_fn
        self.cmd_timeout_sec = cmd_timeout_sec
        self.dir = ws.session_dir(session_id)

    # ------------------------------------------------------------------ #
    # 统一分发
    # ------------------------------------------------------------------ #
    def dispatch(self, name: str, arguments: "str | dict") -> dict:
        """按名分发原语；arguments 兼容 JSON 字符串；任何异常转 ok=False。"""
        if isinstance(arguments, str):
            arguments = json.loads(arguments) if arguments.strip() else {}
        op = getattr(self, f"_op_{name}", None)
        if not isinstance(name, str) or not name.isidentifier() or op is None:
            return {"ok": False, "error": f"unknown tool: {name!r}"}
        try:
            return op(**arguments)
        except PathEscapeError as exc:
            return {"ok": False, "error": f"PATH_FORBIDDEN: {exc}"}
        except TypeError as exc:
            return {"ok": False, "error": f"BAD_ARGS: {exc}"}
        except Exception as exc:  # noqa: BLE001 —— 观察必须回传而非崩溃
            logger.warning("code tool %s failed: %s", name, exc)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------ #
    # 文件五原语
    # ------------------------------------------------------------------ #
    def _op_read_file(self, path: str, offset: int = 1, limit: int = 2000) -> dict:
        """读取文本文件，返回窗口内容与总行数。"""
        p = self.ws.resolve_safe(self.session_id, path)
        if not p.is_file():
            return {"ok": False, "error": f"NOT_FOUND: {path}"}
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        window = lines[max(offset - 1, 0): offset - 1 + limit]
        return {"ok": True, "content": "\n".join(window), "total_lines": len(lines)}

    def _op_write_file(self, path: str, content: str) -> dict:
        """整文件覆写（UTF-8），自动建父目录。"""
        p = self.ws.resolve_safe(self.session_id, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path, "bytes": len(content.encode("utf-8"))}

    def _op_edit_file(self, path: str, old_str: str, new_str: str) -> dict:
        """精确替换；old_str 必须恰好出现一次（0 次 NOT_FOUND，多次 NOT_UNIQUE）。"""
        p = self.ws.resolve_safe(self.session_id, path)
        if not p.is_file():
            return {"ok": False, "error": f"NOT_FOUND: {path}"}
        text = p.read_text(encoding="utf-8", errors="replace")
        n = text.count(old_str)
        if n == 0:
            return {"ok": False, "error": f"NOT_FOUND: old_str not in {path}"}
        if n > 1:
            return {"ok": False, "error": f"NOT_UNIQUE: old_str occurs {n} times in {path}"}
        p.write_text(text.replace(old_str, new_str, 1), encoding="utf-8")
        return {"ok": True, "path": path}

    def _op_list_dir(self, path: str = ".") -> dict:
        """列出目录项（name/type/size），目录在前。"""
        d = self.ws.resolve_safe(self.session_id, path)
        if not d.is_dir():
            return {"ok": False, "error": f"NOT_FOUND: {path}"}
        entries = [
            {"name": c.name, "type": "dir" if c.is_dir() else "file",
             "size": 0 if c.is_dir() else c.stat().st_size}
            for c in d.iterdir()
        ]
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        return {"ok": True, "entries": entries[:200]}

    def _op_search_files(self, pattern: str, path: str = ".", glob: str = "*") -> dict:
        """递归 grep：正则匹配行，glob 过滤文件名，最多 100 条命中。"""
        rx = re.compile(pattern)
        base = self.ws.resolve_safe(self.session_id, path)
        matches: list[dict] = []
        for f in sorted(base.rglob(glob)):
            if not f.is_file() or f.stat().st_size > 2_000_000:
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        matches.append({
                            "path": str(f.relative_to(self.dir)).replace("\\", "/"),
                            "line": i, "text": line.strip()[:200],
                        })
                        if len(matches) >= 100:
                            return {"ok": True, "matches": matches, "count": len(matches)}
            except OSError:
                continue
        return {"ok": True, "matches": matches, "count": len(matches)}

    # ------------------------------------------------------------------ #
    # 命令执行
    # ------------------------------------------------------------------ #
    def _op_run_cmd(self, cmd: list[str]) -> dict:
        """三态执行：block 拒 / need_approval 走 approve_fn / allow 直跑。"""
        verdict, reason = CommandPolicy.verdict(cmd)
        if verdict == "block":
            logger.warning("run_cmd blocked: %r (%s)", cmd, reason)
            return {"ok": False, "error": f"BLOCKED: {reason}", "cmd": cmd}
        if verdict == "need_approval":
            if self.approve_fn is None or not self.approve_fn(list(cmd)):
                return {"ok": False, "error": "APPROVAL_DENIED", "cmd": cmd}
        proc = subprocess.run(
            cmd, cwd=str(self.dir), capture_output=True, text=True,
            timeout=self.cmd_timeout_sec, shell=False,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[:OBS_LIMIT],
            "stderr": proc.stderr[:2000],
        }
