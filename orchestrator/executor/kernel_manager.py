"""沙箱 Kernel 生命周期管理 + 按 session_id 复用 + 代码执行通道。

- acquire(session_id) → 复用或新建 KernelHandle
- release(session_id) → 显式销毁
- idle_sweep() → 清理 idle 超时的 Kernel
- exec_code(session_id, code) → 容器内真实执行 Python（Phase 13 T2）

T2 采用 docker exec 直执行（非 Jupyter TCP kernel，见 Phase 13 spec）：
代码经 stdin 写入容器 tmpfs（零转义），镜像内固化 harness /opt/run_user.py
解析出 stdout + 末表达式 repr，宿主侧再把 repr 还原为原生 JSON 安全类型
（dict/列表/数字/bool/None——真机 2026-08-31：下游节点 `data = n2.result`
拿到字符串字面量后 `data["k"]` 抛 TypeError）。
"""
from __future__ import annotations

import ast
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from orchestrator.executor.sandbox import DockerSandbox
from shared.errors import SandboxTimeoutError, SandboxUnavailableError

# 与 sandbox/run_user.py 的 SENTINEL 保持一致
_SENTINEL = "\n###RESULT###\n"

# 还原快速门槛：JSON 安全字面量的 repr 只可能以这些字符开头
# （含字符串 repr 的引号；True/False/None 大写；nan/inf 非合法输入）
_LITERAL_HEADS = frozenset("'\"([{[-+.0123456789TFN")


def _revive_result(text: str) -> Any:
    """末表达式 repr 字符串 → 原生对象（仅 JSON 安全类型），失败原样返回。

    - dict/list/数字/bool/None 还原为原生值 → 下游注入合法字面量、
      IM 关键输出与 DB 持久化均更友好；
    - tuple 转 list（JSON 持久化安全）；set 等不可序列化 repr 不还原；
    - numpy 数组等对象 repr（如 array([...])）不以门槛字符开头，自动跳过。
    """
    t = text.strip()
    if not t or t[0] not in _LITERAL_HEADS:
        return text
    try:
        v = ast.literal_eval(t)
    except (ValueError, SyntaxError):
        return text
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, (dict, list, str, int, float, bool, type(None))):
        return v
    return text


@dataclass
class KernelHandle:
    kernel_id: str  # ULID/UUID
    session_id: str
    container_name: str
    started_at: datetime
    last_used_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class KernelPool:
    """Phase 16 起多线程访问（exec_code 调用线程 + idle_sweep 守护线程），
    全部句柄表操作持锁；docker exec 等慢操作在锁外执行。"""

    def __init__(self, sandbox: DockerSandbox, idle_timeout_sec: int = 1800) -> None:
        self._sandbox = sandbox
        self._idle_timeout = timedelta(seconds=idle_timeout_sec)
        self._handles: dict[str, KernelHandle] = {}
        self._lock = threading.Lock()

    def acquire(self, session_id: str) -> KernelHandle:
        with self._lock:
            existing = self._handles.get(session_id)
            if existing is not None:
                existing.last_used_at = datetime.now(UTC)
                return existing
        container_name = self._sandbox.start(session_id)  # 慢操作锁外
        handle = KernelHandle(
            kernel_id=uuid.uuid4().hex,
            session_id=session_id,
            container_name=container_name,
            started_at=datetime.now(UTC),
        )
        with self._lock:
            # 并发 acquire 竞争：后建者让位（丢弃自己的容器，复用先入表者）
            winner = self._handles.get(session_id)
            if winner is not None:
                loser_name = handle.container_name
            else:
                self._handles[session_id] = handle
                winner = handle
        if winner is not handle:
            self._sandbox.stop(loser_name)  # 清理竞争败者容器
        return winner

    def release(self, session_id: str) -> None:
        with self._lock:
            h = self._handles.pop(session_id, None)
        if h:
            self._sandbox.stop(h.container_name)  # 慢操作锁外

    def touch(self, session_id: str) -> None:
        with self._lock:
            h = self._handles.get(session_id)
            if h:
                h.last_used_at = datetime.now(UTC)

    def idle_sweep(self) -> int:
        with self._lock:
            now = datetime.now(UTC)
            expired = [
                sid
                for sid, h in self._handles.items()
                if now - h.last_used_at > self._idle_timeout
            ]
            # 先从表里摘除（锁内），stop 在锁外执行
            handles = [self._handles.pop(sid) for sid in expired]
        for h in handles:
            self._sandbox.stop(h.container_name)
        return len(expired)

    def get(self, session_id: str) -> Optional[KernelHandle]:
        return self._handles.get(session_id)

    # === Phase 13 T2：容器内真实执行 ===

    def exec_code(
        self, session_id: str, code: str, timeout_sec: int = 60
    ) -> dict[str, Any]:
        """在 session 容器内执行 Python 代码。

        返回 {"stdout", "result", "error_code"?, "error_message"?}：
        - 基础设施故障抛 SandboxUnavailableError / SandboxTimeoutError
        - 用户代码异常（退出码非 0）→ error_code=PY_RUNTIME_ERROR + stderr 尾部
        - result 为末顶层表达式的原生值（dict/list/数字/bool/None；
          repr 无法安全还原时为字符串，无表达式为 None）
        """
        # 空 code 防御：空文件执行 rc=0 会伪装成 success（真机 2026-08-30）
        if not isinstance(code, str) or not code.strip():
            return {
                "stdout": "",
                "result": None,
                "error_code": "INVALID_INPUT",
                "error_message": "code 为空——引用解析失败或模型未生成代码",
            }
        handle = self.acquire(session_id)
        path = f"/tmp/r_{uuid.uuid4().hex}.py"
        try:
            write = self._sandbox.exec(
                handle.container_name,
                ["sh", "-c", f"cat > {path}"],
                input_text=code, timeout_sec=15,
            )
            if write.returncode != 0:
                raise SandboxUnavailableError(
                    f"write code failed: rc={write.returncode} "
                    f"stderr={(write.stderr or '')[:200]}"
                )
            proc = self._sandbox.exec(
                handle.container_name,
                ["python", "/opt/run_user.py", path],
                timeout_sec=timeout_sec,
            )
        except subprocess.TimeoutExpired as e:
            # 超时即重建容器，防容器内残留进程污染后续执行（spec §3.4）
            self.release(session_id)
            raise SandboxTimeoutError(
                f"exec exceeded {timeout_sec}s: {e}"
            ) from e
        except SandboxUnavailableError:
            self.release(session_id)
            raise

        stdout, result = self._split_sentinel(proc.stdout or "")
        if proc.returncode != 0:
            return {
                "stdout": stdout,
                "result": None,
                "error_code": "PY_RUNTIME_ERROR",
                "error_message": (proc.stderr or "")[-300:],
            }
        self.touch(session_id)
        return {"stdout": stdout, "result": _revive_result(result)}

    @staticmethod
    def _split_sentinel(stdout: str) -> tuple[str, str]:
        """按 sentinel 切分 harness 输出：前段=用户 stdout，后段=result repr。"""
        if _SENTINEL in stdout:
            head, _, tail = stdout.partition(_SENTINEL)
            return head, tail
        # harness 未打 sentinel（异常退出等）——整段视为 stdout
        return stdout, "None"
