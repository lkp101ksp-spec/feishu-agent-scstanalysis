import datetime as dt
import subprocess

import pytest

from orchestrator.executor.kernel_manager import KernelPool
from shared.errors import SandboxTimeoutError, SandboxUnavailableError


class FakeSandbox:
    def __init__(self):
        self.started: list[str] = []
        self.stopped: list[str] = []

    def start(self, session_id):
        cid = f"c_{session_id}"
        self.started.append(cid)
        return cid

    def stop(self, container_name):
        self.stopped.append(container_name)


def test_acquire_new_kernel_creates_container():
    sandbox = FakeSandbox()
    pool = KernelPool(sandbox=sandbox, idle_timeout_sec=1800)
    handle = pool.acquire("s1")
    assert handle.session_id == "s1"
    assert handle.container_name == "c_s1"
    assert sandbox.started == ["c_s1"]


def test_acquire_existing_returns_same_handle():
    sandbox = FakeSandbox()
    pool = KernelPool(sandbox=sandbox, idle_timeout_sec=1800)
    h1 = pool.acquire("s1")
    h2 = pool.acquire("s1")
    assert h1 is h2
    assert sandbox.started == ["c_s1"]


def test_idle_sweep_removes_old():
    sandbox = FakeSandbox()
    pool = KernelPool(sandbox=sandbox, idle_timeout_sec=1800)
    h = pool.acquire("s1")
    h.last_used_at = dt.datetime.utcnow() - dt.timedelta(seconds=3600)
    removed = pool.idle_sweep()
    assert removed == 1
    assert sandbox.stopped == ["c_s1"]


def test_idle_sweep_keeps_recent():
    sandbox = FakeSandbox()
    pool = KernelPool(sandbox=sandbox, idle_timeout_sec=1800)
    pool.acquire("s1")
    assert pool.idle_sweep() == 0
    assert sandbox.stopped == []


# === Phase 13 T2：exec_code 执行通道 ===

def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class ExecFakeSandbox(FakeSandbox):
    """按调用序返回预置 exec 结果；记录全部调用参数。"""

    def __init__(self, exec_results=None):
        super().__init__()
        self.exec_calls: list[dict] = []
        self._exec_results = exec_results or []

    def exec(self, container_name, cmd, *, timeout_sec=60, input_text=None):
        self.exec_calls.append({
            "container": container_name, "cmd": cmd,
            "timeout": timeout_sec, "input": input_text,
        })
        return self._exec_results.pop(0)


def test_exec_code_roundtrip_stdout_and_result():
    """正常路径：stdin 写入 + harness 执行 + sentinel 切分。"""
    sandbox = ExecFakeSandbox(exec_results=[
        _completed(),  # cat > path
        _completed(stdout="hello\n\n###RESULT###\n1267650600228229401496703205376"),
    ])
    pool = KernelPool(sandbox=sandbox, idle_timeout_sec=1800)
    out = pool.exec_code("s1", "print('hello')\n2**100", timeout_sec=30)

    assert out == {"stdout": "hello\n", "result": "1267650600228229401496703205376"}
    # 第一次调用：stdin 写代码，零转义
    w = sandbox.exec_calls[0]
    assert w["input"] == "print('hello')\n2**100"
    assert w["cmd"][0] == "sh"
    # 第二次调用：跑 harness，超时透传
    r = sandbox.exec_calls[1]
    assert r["cmd"][:2] == ["python", "/opt/run_user.py"]
    assert r["timeout"] == 30
    assert r["input"] is None
    # 容器复用（不重复 start）
    assert sandbox.started == ["c_s1"]


def test_exec_code_user_error_returns_py_runtime_error():
    """用户代码异常（退出码非 0）→ error_code=PY_RUNTIME_ERROR + stderr 尾部。"""
    sandbox = ExecFakeSandbox(exec_results=[
        _completed(),
        _completed(stdout="before crash", stderr="ZeroDivisionError: division by zero",
                   returncode=1),
    ])
    pool = KernelPool(sandbox=sandbox)
    out = pool.exec_code("s1", "1/0")
    assert out["error_code"] == "PY_RUNTIME_ERROR"
    assert "ZeroDivisionError" in out["error_message"]
    assert out["result"] is None
    # 出错不销毁容器（可继续执行后续代码）
    assert sandbox.stopped == []


def test_exec_code_empty_code_rejected_without_sandbox():
    """空/None code 直接拒绝——空文件执行 rc=0 会伪装 success（真机 2026-08-30）。"""
    sandbox = ExecFakeSandbox(exec_results=[_completed(), _completed()])
    pool = KernelPool(sandbox=sandbox)
    for bad in (None, "", "   \n"):
        out = pool.exec_code("s1", bad)
        assert out["error_code"] == "INVALID_INPUT"
    # 不碰沙箱（不 start、不 exec）
    assert sandbox.started == []
    assert sandbox.exec_calls == []


def test_exec_code_timeout_releases_container():
    """exec 超时 → SandboxTimeoutError 且容器重建（防残留进程污染）。"""

    class TimeoutSandbox(ExecFakeSandbox):
        def exec(self, container_name, cmd, *, timeout_sec=60, input_text=None):
            self.exec_calls.append({"cmd": cmd})
            if cmd[0] == "python":
                raise subprocess.TimeoutExpired(cmd="docker", timeout=timeout_sec)
            return _completed()

    sandbox = TimeoutSandbox()
    pool = KernelPool(sandbox=sandbox)
    with pytest.raises(SandboxTimeoutError):
        pool.exec_code("s1", "while True: pass", timeout_sec=5)
    assert sandbox.stopped == ["c_s1"]  # 已销毁
    assert pool.get("s1") is None


def test_exec_code_write_failure_unavailable():
    """代码写入失败 → SandboxUnavailableError 且容器销毁。"""
    sandbox = ExecFakeSandbox(exec_results=[
        _completed(stderr="disk full", returncode=1),
    ])
    pool = KernelPool(sandbox=sandbox)
    with pytest.raises(SandboxUnavailableError):
        pool.exec_code("s1", "print(1)")
    assert sandbox.stopped == ["c_s1"]


def test_split_sentinel_missing_falls_back():
    """harness 未打 sentinel（异常路径）——整段视为 stdout。"""
    stdout, result = KernelPool._split_sentinel("raw output without sentinel")
    assert stdout == "raw output without sentinel"
    assert result == "None"
