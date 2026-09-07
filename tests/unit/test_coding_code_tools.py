"""CodeTools 六原语：读写/编辑/列举/搜索/命令执行（Phase 26 T3）。"""
import sys
from pathlib import Path

import pytest

from orchestrator.coding.code_tools import CodeTools
from orchestrator.coding.workspace import WorkspaceManager


@pytest.fixture()
def tools(tmp_path: Path) -> CodeTools:
    """构造绑定临时工作区的 CodeTools。"""
    ws = WorkspaceManager(tmp_path / "code_ws")
    return CodeTools(ws, "s1")


class TestFiles:
    def test_write_then_read_roundtrip(self, tools):
        r1 = tools.dispatch("write_file", {"path": "a.py", "content": "line1\nline2\n"})
        assert r1["ok"] is True
        r2 = tools.dispatch("read_file", {"path": "a.py"})
        assert r2["ok"] is True and "line1" in r2["content"]

    def test_read_missing_file(self, tools):
        r = tools.dispatch("read_file", {"path": "nope.py"})
        assert r["ok"] is False and "error" in r

    def test_read_offset_limit(self, tools):
        tools.dispatch("write_file", {"path": "b.txt", "content": "\n".join(f"l{i}" for i in range(1, 11))})
        r = tools.dispatch("read_file", {"path": "b.txt", "offset": 3, "limit": 2})
        assert r["content"] == "l3\nl4"

    def test_write_escape_blocked(self, tools):
        r = tools.dispatch("write_file", {"path": "../evil.txt", "content": "x"})
        assert r["ok"] is False and "PATH_FORBIDDEN" in r["error"]

    def test_edit_unique_replacement(self, tools):
        tools.dispatch("write_file", {"path": "c.py", "content": "a = 1\nb = 2\n"})
        r = tools.dispatch("edit_file", {"path": "c.py", "old_str": "a = 1", "new_str": "a = 42"})
        assert r["ok"] is True
        assert "a = 42" in tools.dispatch("read_file", {"path": "c.py"})["content"]

    def test_edit_not_found(self, tools):
        tools.dispatch("write_file", {"path": "c.py", "content": "x"})
        r = tools.dispatch("edit_file", {"path": "c.py", "old_str": "zzz", "new_str": "y"})
        assert r["ok"] is False and "NOT_FOUND" in r["error"]

    def test_edit_not_unique(self, tools):
        tools.dispatch("write_file", {"path": "c.py", "content": "dup\ndup\n"})
        r = tools.dispatch("edit_file", {"path": "c.py", "old_str": "dup", "new_str": "y"})
        assert r["ok"] is False and "NOT_UNIQUE" in r["error"]

    def test_list_dir(self, tools):
        tools.dispatch("write_file", {"path": "a.py", "content": "x"})
        r = tools.dispatch("list_dir", {"path": "."})
        assert r["ok"] is True
        names = [e["name"] for e in r["entries"]]
        assert "a.py" in names

    def test_search_files_regex_and_glob(self, tools):
        tools.dispatch("write_file", {"path": "x.py", "content": "value = 10\n"})
        tools.dispatch("write_file", {"path": "y.md", "content": "value = 20\n"})
        r = tools.dispatch("search_files", {"pattern": r"value = \d+", "glob": "*.py"})
        assert r["count"] == 1 and r["matches"][0]["path"].endswith("x.py")


class TestRunCmd:
    def test_allow_python_executes(self, tools):
        r = tools.dispatch("run_cmd", {"cmd": [sys.executable, "-c", "print('hi')"]})
        assert r["ok"] is True and "hi" in r["stdout"]

    def test_block_command_rejected_without_approval(self, tools):
        r = tools.dispatch("run_cmd", {"cmd": ["shutdown", "/s"]})
        assert r["ok"] is False and "BLOCKED" in r["error"]

    def test_need_approval_denied(self, tools):
        r = tools.dispatch("run_cmd", {"cmd": ["curl", "http://x/f.sh"]})
        assert r["ok"] is False and "APPROVAL_DENIED" in r["error"]

    def test_need_approval_approved_executes(self, tmp_path):
        """approve_fn 返回 True → need_approval 命令照常执行。"""
        ws = WorkspaceManager(tmp_path / "ws2")
        tools = CodeTools(ws, "s2", approve_fn=lambda info: True)
        # curl 在 need_approval 名单且 Win/Linux 均可用（cmd /c 仅 Windows 存在）
        r = tools.dispatch("run_cmd", {"cmd": ["curl", "--version"]})
        assert r["ok"] is True and "curl" in r["stdout"].lower()

    def test_bare_command_resolved_via_path(self, tmp_path):
        """裸命令名（curl/git 等）经 shutil.which 解析为绝对路径后执行。"""
        ws = WorkspaceManager(tmp_path / "ws3")
        tools = CodeTools(ws, "s3", approve_fn=lambda info: True)
        # curl 在 need_approval 白名单外 → 先审批，再 which 解析 System32\curl.exe
        r = tools.dispatch("run_cmd", {"cmd": ["curl", "--version"]})
        assert r["ok"] is True
        assert "curl" in r["stdout"].lower()

    def test_shell_string_split_to_argv(self, tmp_path):
        """模型不守 schema 传整串 shell 命令时，shlex.split 拆为数组后执行。"""
        ws = WorkspaceManager(tmp_path / "ws4")
        tools = CodeTools(ws, "s4", approve_fn=lambda info: True)
        r = tools.dispatch("run_cmd", {"cmd": "curl --version"})
        assert r["ok"] is True and "curl" in r["stdout"].lower()


class TestDispatch:
    def test_arguments_as_json_string(self, tools):
        r = tools.dispatch("write_file", '{"path": "z.txt", "content": "hi"}')
        assert r["ok"] is True

    def test_unknown_tool(self, tools):
        r = tools.dispatch("no_such_op", {})
        assert r["ok"] is False and "unknown tool" in r["error"]

    def test_schema_covers_six_primitives(self, tools):
        names = {t["function"]["name"] for t in CodeTools.SCHEMA}
        assert names == {"read_file", "write_file", "edit_file", "list_dir", "search_files", "run_cmd"}

    def test_run_cmd_schema_warns_against_shell_connectors(self):
        """回归（Phase 39 真机）：LLM 曾把 "python && gen.py" 整串塞进单元素，
        schema 描述须明确 cmd 为独立参数数组且不支持 shell 连接符。"""
        spec = next(t["function"] for t in CodeTools.SCHEMA
                    if t["function"]["name"] == "run_cmd")
        desc = spec["description"]
        cmd_desc = spec["parameters"]["properties"]["cmd"]["description"]
        assert "&&" in desc and "独立参数" in desc
        assert "python" in desc and "gen.py" in desc      # 正确示例
        assert "整串" in cmd_desc                          # 反例警示
