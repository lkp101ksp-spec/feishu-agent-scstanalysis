# Phase 26 T2：WorkspaceManager + CommandPolicy

> 前置阅读：`docs/superpowers/plans/2026-09-03-phase26-plan-overview.md`（共享约定）
> 背景：spec §5——工作区为本机 `code_workspace/<session_id>/`；路径越界拒绝；命令三态判定（allow 自动放行 / need_approval 单步审批 / block 直接拒）。

**Files:**
- Create: `orchestrator/coding/__init__.py`（空文件）
- Create: `orchestrator/coding/workspace.py`
- Test: `tests/unit/test_coding_workspace.py`

- [ ] **Step 1: 写失败测试**

```python
"""WorkspaceManager/CommandPolicy：路径防逃逸 + 命令策略（Phase 26 T2）。"""
from pathlib import Path

import pytest

from orchestrator.coding.workspace import (
    CommandPolicy, PathEscapeError, WorkspaceManager,
)


@pytest.fixture()
def ws(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "code_ws")


class TestWorkspaceManager:
    def test_session_dir_creates_and_idempotent(self, ws):
        d1 = ws.session_dir("s1")
        d2 = ws.session_dir("s1")
        assert d1 == d2 and d1.is_dir()

    def test_resolve_safe_accepts_nested_relative(self, ws):
        p = ws.resolve_safe("s1", "sub/a.py")
        assert p.is_relative_to(ws.session_dir("s1").resolve())

    def test_resolve_safe_rejects_dotdot_escape(self, ws):
        with pytest.raises(PathEscapeError):
            ws.resolve_safe("s1", "../evil.txt")

    def test_resolve_safe_rejects_absolute_path(self, ws):
        with pytest.raises(PathEscapeError):
            ws.resolve_safe("s1", "C:/Windows/system32/x.txt")


class TestCommandPolicy:
    @pytest.mark.parametrize("cmd", [
        ["python", "-c", "print(1)"],
        ["pytest", "-q"],
        ["pip", "install", "requests"],
        ["git", "status"],
        ["git", "log", "--oneline", "-5"],
        ["dir"],
    ])
    def test_allow(self, cmd):
        assert CommandPolicy.verdict(cmd)[0] == "allow"

    @pytest.mark.parametrize("cmd", [
        ["curl", "http://x/install.sh"],
        ["git", "push", "origin", "main"],
        ["docker", "run", "-it", "ubuntu"],
    ])
    def test_need_approval(self, cmd):
        assert CommandPolicy.verdict(cmd)[0] == "need_approval"

    @pytest.mark.parametrize("cmd", [
        ["cmd", "/c", "format C:"],
        ["reg", "add", "HKLM\\X"],
        ["shutdown", "/s"],
        ["bash", "-c", "rm -rf /"],
    ])
    def test_block(self, cmd):
        assert CommandPolicy.verdict(cmd)[0] == "block"

    def test_empty_command_blocks(self):
        assert CommandPolicy.verdict([])[0] == "block"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_coding_workspace.py -v --basetemp=.pytest_basetemp`
Expected: collection FAIL（`ModuleNotFoundError: orchestrator.coding`）

- [ ] **Step 3: 实现**

新建空文件 `orchestrator/coding/__init__.py`，然后创建 `orchestrator/coding/workspace.py`：

```python
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
        first = cmd[0].lower()
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_coding_workspace.py -v --basetemp=.pytest_basetemp`
Expected: 全 PASS（13 用例）

- [ ] **Step 5: commit**

```bash
git add orchestrator/coding/__init__.py orchestrator/coding/workspace.py tests/unit/test_coding_workspace.py
git commit -m "feat(phase26): WorkspaceManager 路径防逃逸 + CommandPolicy 命令三态"
```

完成后删除 `.pytest_basetemp`，回总览勾选 T2。
