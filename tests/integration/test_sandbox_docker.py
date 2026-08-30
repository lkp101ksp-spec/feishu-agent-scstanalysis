"""Phase 13 T2 真容器集成测试（需 Docker + kernel 镜像，缺则自动 skip）。

镜像构建：
    docker build -t feishu-research-agent/kernel:latest sandbox -f sandbox/kernel.Dockerfile
"""
import shutil
import subprocess

import pytest

from config.settings import Settings
from orchestrator.executor.kernel_manager import KernelPool
from orchestrator.executor.sandbox import DockerSandbox, DockerSandboxConfig

_IMAGE = "feishu-research-agent/kernel:latest"


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10)
    except Exception:
        return False
    inspect = subprocess.run(
        ["docker", "image", "inspect", _IMAGE],
        capture_output=True, timeout=10,
    )
    return inspect.returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_ready(), reason="docker 不可用或 kernel 镜像未构建"
)


@pytest.fixture(scope="module")
def pool() -> KernelPool:
    cfg = DockerSandboxConfig(
        image=_IMAGE, cpu_limit=1.0, memory_limit="512m",
        pids_limit=64, network_mode="none", workspace_path="/tmp/x",
    )
    p = KernelPool(sandbox=DockerSandbox(cfg), idle_timeout_sec=1800)
    yield p
    for sid in list(p._handles):
        p.release(sid)


def test_real_container_print_and_result(pool):
    out = pool.exec_code("itest", "print('hello')\n2**100", timeout_sec=60)
    assert out.get("error_code") is None
    assert out["stdout"] == "hello\n"
    assert out["result"] == "1267650600228229401496703205376"


def test_real_container_numpy_available(pool):
    out = pool.exec_code(
        "itest", "import numpy as np\nnp.arange(5).sum()", timeout_sec=60
    )
    assert out.get("error_code") is None
    assert out["result"] == "10"


def test_real_container_user_error(pool):
    out = pool.exec_code("itest", "1/0", timeout_sec=60)
    assert out["error_code"] == "PY_RUNTIME_ERROR"
    assert "ZeroDivisionError" in out["error_message"]


def test_real_container_timeout_rebuilds(pool):
    import time

    from shared.errors import SandboxTimeoutError

    with pytest.raises(SandboxTimeoutError):
        pool.exec_code("itest", "while True: pass", timeout_sec=5)
    # 容器已销毁：下一次执行会重建并正常完成
    time.sleep(1)
    out = pool.exec_code("itest", "41+1", timeout_sec=60)
    assert out["result"] == "42"
