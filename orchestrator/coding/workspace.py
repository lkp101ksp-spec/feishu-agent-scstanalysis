"""coding 会话工作区：目录生命周期 + 路径防逃逸 + 命令策略（Phase 26）。

spec 2026-09-03-phase26-code-agent-design §5：
- 工作区为本机 code_workspace/<session_id>/（跨任务持久，/code clear 清理）
- 所有文件操作路径必须 resolve 后落在会话目录内（PathEscapeError）
- run_cmd 三态：allow 白名单直跑 / need_approval 单步审批 / block 直接拒
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PathEscapeError(Exception):
    """路径越界：resolve 后不在会话工作区内。"""


class CommandPolicy:
    """命令三态判定（v1 静态表；黑名单 > 白名单）。"""

    # 前缀白名单：开发命令直跑（python/pytest/pip 及只读 git 子命令）
    ALLOW_FIRST = ("python", "python3", "py", "pytest", "pip", "pip3")
    GIT_READONLY = ("status", "log", "diff", "show", "branch")
    # 只读查看类
    READONLY_FIRST = ("dir", "ls", "type", "cat", "where", "which", "findstr")
    # 危险子串（join 后小写匹配）：直接拒，不发审批卡
    BLOCK_SUBSTR = (
        "format ", "shutdown", "reg add", "reg delete", "rm -rf /",
        "rd /s", "del /f /s", "remove-item -recurse -force c:\\",
        "mkfs", "diskpart",
    )

    @classmethod
    def verdict(cls, cmd: list[str]) -> tuple[str, str]:
        """返回 (verdict, reason)；verdict ∈ allow/need_approval/block。"""
        if not cmd:
            return "block", "empty command"
        joined = " ".join(cmd).lower()
        for b in cls.BLOCK_SUBSTR:
            if b in joined:
                return "block", f"blacklisted pattern: {b!r}"
        first = Path(cmd[0]).stem.lower()
        if first in cls.ALLOW_FIRST:
            return "allow", ""
        if first == "git":
            sub = cmd[1].lower() if len(cmd) > 1 else ""
            if sub in cls.GIT_READONLY:
                return "allow", ""
        if first in cls.READONLY_FIRST:
            return "allow", ""
        return "need_approval", f"command not in allowlist: {first}"


class WorkspaceManager:
    """会话工作区目录管理。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def session_dir(self, session_id: str) -> Path:
        """获取（必要时创建）会话工作区目录；幂等。"""
        d = self.root / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def resolve_safe(self, session_id: str, rel: str) -> Path:
        """解析会话内相对路径；越界（../、绝对盘符）抛 PathEscapeError。"""
        base = self.session_dir(session_id).resolve()
        # Windows 下 "/".join 语义：绝对盘符路径会整体替换 base，仍能被
        # is_relative_to 检出越界
        p = (base / rel).resolve()
        if not p.is_relative_to(base):
            logger.warning("path escape blocked: session=%s rel=%r", session_id, rel)
            raise PathEscapeError(f"path escapes workspace: {rel!r}")
        return p
