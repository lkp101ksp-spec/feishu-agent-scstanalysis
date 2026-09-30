"""WorkspaceManager/CommandPolicy：路径防逃逸 + 命令策略（Phase 26 T2）。"""
from pathlib import Path

import pytest

from orchestrator.coding.workspace import (
    CommandPolicy,
    PathEscapeError,
    WorkspaceManager,
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


class TestResolveRead:
    """只读路径：会话目录内照常；越界但落在 bio_root 内放行；bio_root 外仍拒。"""

    def _mk(self, tmp_path):
        bio = tmp_path / "bio_workspace"
        (bio / "aaaaaaaaaaaa").mkdir(parents=True)
        (bio / "aaaaaaaaaaaa" / "report.txt").write_text("qc", encoding="utf-8")
        ws = WorkspaceManager(bio / "_code", bio_root=bio)
        ws.session_dir("s1")
        return ws, bio

    def test_session_relative_still_works(self, tmp_path):
        ws, bio = self._mk(tmp_path)
        ws.session_dir("s1").joinpath("a.txt").write_text("x", encoding="utf-8")
        assert ws.resolve_read("s1", "a.txt").name == "a.txt"

    def test_bio_root_absolute_readable(self, tmp_path):
        ws, bio = self._mk(tmp_path)
        target = str(bio / "aaaaaaaaaaaa" / "report.txt")
        assert ws.resolve_read("s1", target).name == "report.txt"

    def test_bio_root_relative_dotdot_readable(self, tmp_path):
        ws, bio = self._mk(tmp_path)
        p = ws.resolve_read("s1", "../../aaaaaaaaaaaa/report.txt")
        assert p.name == "report.txt"

    def test_outside_bio_root_blocked(self, tmp_path):
        ws, bio = self._mk(tmp_path)
        with pytest.raises(PathEscapeError):
            ws.resolve_read("s1", str(tmp_path / "elsewhere" / "x.txt"))

    def test_no_bio_root_falls_back_strict(self, tmp_path):
        ws = WorkspaceManager(tmp_path / "ws")
        ws.session_dir("s1")
        with pytest.raises(PathEscapeError):
            ws.resolve_read("s1", "../other/x.txt")
