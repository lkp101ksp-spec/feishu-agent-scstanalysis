import asyncio

from orchestrator.background.task_runner import BackgroundTaskRunner


async def test_runner_calls_task():
    called = []

    async def task():
        called.append(1)

    runner = BackgroundTaskRunner([task], interval_sec=0.05)
    runner.start()
    await asyncio.sleep(0.15)
    runner.stop()
    assert len(called) >= 2


async def test_runner_handles_exception():
    called = []

    async def task_ok():
        called.append("ok")

    async def task_fail():
        raise RuntimeError("boom")

    runner = BackgroundTaskRunner([task_ok, task_fail], interval_sec=0.05)
    runner.start()
    await asyncio.sleep(0.15)
    runner.stop()
    # task_ok 仍被多次调用
    assert "ok" in called


def test_runner_init_stores_tasks():
    async def t():
        pass
    runner = BackgroundTaskRunner([t], interval_sec=10)
    assert len(runner._tasks) == 1