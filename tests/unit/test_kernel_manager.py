import datetime as dt

from orchestrator.executor.kernel_manager import KernelPool


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
