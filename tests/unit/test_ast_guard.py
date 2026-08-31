import pytest

from orchestrator.tools.ast_guard import ASTGuard
from shared.errors import ToolBlockedError

guard = ASTGuard()


def test_blocks_os_system():
    with pytest.raises(ToolBlockedError, match="os.system"):
        guard.check("import os\nos.system('rm -rf /')")


def test_blocks_subprocess_run():
    with pytest.raises(ToolBlockedError, match="subprocess"):
        guard.check("from subprocess import run\nrun(['ls'])")


def test_blocks_socket():
    with pytest.raises(ToolBlockedError, match="socket"):
        guard.check("import socket\ns = socket.socket()")


def test_blocks_ctypes():
    with pytest.raises(ToolBlockedError, match="ctypes"):
        guard.check("import ctypes\nctypes.CDLL('libc.so.6')")


def test_allows_safe_pandas():
    guard.check("import pandas as pd\ndf = pd.DataFrame({'a': [1,2]}); print(df.describe())")


def test_allows_safe_open_in_workspace():
    guard.check("with open('/workspace/data.csv') as f: print(f.read())")


def test_blocks_nested_subprocess_popen():
    with pytest.raises(ToolBlockedError, match="subprocess"):
        guard.check("from subprocess import Popen\nPopen(['ls'])")


def test_syntax_error_message_carries_position_and_code_head():
    """语法错误附位置 + code 头部（真机 2026-08-31 b1_tt1 排障可读性）。"""
    bad = "print(f\"比例：{{'a': 1}['k']}%\")"  # f-string 花括号转义错乱
    with pytest.raises(ToolBlockedError) as ei:
        guard.check(bad)
    msg = str(ei.value)
    assert "syntax error" in msg
    assert "行" in msg  # 行号/列位置
    assert "code 头部" in msg
    assert "print(f" in msg  # 原文头部可见


def test_empty_code_safe():
    guard.check("")
