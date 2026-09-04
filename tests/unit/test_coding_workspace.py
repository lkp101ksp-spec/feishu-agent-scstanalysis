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
