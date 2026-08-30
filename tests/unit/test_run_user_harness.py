"""sandbox/run_user.py harness 本体测试（本机真实 python 执行）。

harness 是纯标准库脚本，无需容器即可验证输出协议。
"""
import subprocess
import sys
from pathlib import Path

_HARNESS = Path(__file__).parents[2] / "sandbox" / "run_user.py"


def _run(code: str) -> subprocess.CompletedProcess:
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        path = f.name
    return subprocess.run(
        [sys.executable, str(_HARNESS), path],
        capture_output=True, text=True, timeout=10,
    )


def test_harness_print_and_last_expr():
    out = _run("print('hello')\n2**100")
    assert out.returncode == 0
    assert out.stdout == "hello\n\n###RESULT###\n1267650600228229401496703205376"


def test_harness_no_last_expr_result_none():
    out = _run("x = 1\nprint(x)")
    assert out.returncode == 0
    assert out.stdout == "1\n\n###RESULT###\nNone"


def test_harness_exception_flushes_stdout_and_nonzero_exit():
    out = _run("print('before')\n1/0")
    assert out.returncode != 0
    assert out.stdout == "before\n"  # 已捕获 stdout 先冲刷，无 sentinel
    assert "ZeroDivisionError" in out.stderr


def test_harness_result_none_for_falsy_value():
    """末表达式值为 0/False/空串时 repr 应正确输出而非 'None'。"""
    out = _run("0")
    assert out.returncode == 0
    assert out.stdout.endswith("###RESULT###\n0")
