"""_ResearchProgressCard 单测（Phase 41）：节流/熔断/禁用回退/三类卡面。

可控时钟注入 now；im 为 MagicMock（send_card 返回 message_id）。
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orchestrator.research_runner import (
    ResearchRunner,
    _fmt_elapsed,
    _ResearchProgressCard,
)
from shared.executor_types import ExecutionState, TaskHandle
from shared.ulid_ import new_ulid


class _Clock:
    """可控单调时钟。"""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += sec


@pytest.fixture()
def card():
    im = MagicMock()
    im.send_card.return_value = "om_progress"
    clock = _Clock()
    c = _ResearchProgressCard(im, "oc_x", "对数据集做双联体检测",
                              min_interval=8.0, now=clock)
    return SimpleNamespace(im=im, clock=clock, card=c)


def _snap(*, success=0, failed=0, denied=0, skipped=0, running=None,
          done=None, total=3):
    counts = {}
    if success:
        counts["success"] = success
    if failed:
        counts["failed"] = failed
    if denied:
        counts["denied"] = denied
    if skipped:
        counts["skipped"] = skipped
    return {"counts": counts, "running": running or [],
            "done": done or [], "total": total}


def _plan(n=3):
    nodes = [SimpleNamespace(node_id=f"n{i}", tool_name=f"tool_{i}",
                             kind="tool") for i in range(1, n + 1)]
    return SimpleNamespace(nodes=nodes)


class TestLifecycle:
    def test_start_sends_planning_card(self, card):
        assert card.card.start() is True
        sent = card.im.send_card.call_args.args[1]
        assert sent["header"] == "研究任务执行中"
        body = sent["elements"][-1]["text"]["content"]
        assert "规划中" in body and "双联体检测" in body

    def test_start_send_raises_disables(self, card):
        card.im.send_card.side_effect = RuntimeError("net down")
        assert card.card.start() is False
        # 禁用后全 no-op：plan_done/tick/finish 均不再触网
        card.card.plan_done(_plan())
        card.card.tick(_snap(success=1))
        card.card.finish("success", {"n1": "success"})
        card.im.update_card.assert_not_called()

    def test_start_empty_message_id_disables(self, card):
        card.im.send_card.return_value = ""
        assert card.card.start() is False
        card.card.tick(_snap(success=1))
        card.im.update_card.assert_not_called()

    def test_never_started_card_is_noop(self, card):
        """_run 兜底路径：未 start 的卡不应向空 message_id 发 PATCH。"""
        card.card.plan_done(_plan())
        card.card.finish("success", {"n1": "success"})
        card.card.finish_error("boom")
        card.im.update_card.assert_not_called()
        card.im.send_card.assert_not_called()

    def test_plan_done_pushes_running_card_immediately(self, card):
        card.card.start()
        card.card.plan_done(_plan(3))
        upd = card.im.update_card.call_args
        assert upd.args[0] == "om_progress"
        body = upd.args[1]["elements"][-1]["text"]["content"]
        assert "执行中" in body and "0/3" in body


class TestTickThrottle:
    def test_terminal_change_updates_immediately(self, card):
        card.card.start()
        card.card.plan_done(_plan())
        assert card.im.update_card.call_count == 1
        # 终态数变化（0→1）：立即刷新，不受 min_interval 限制
        card.card.tick(_snap(success=1, done=[("✓", "n1 tool_1")]))
        assert card.im.update_card.call_count == 2
        body = card.im.update_card.call_args.args[1]["elements"][-1][
            "text"]["content"]
        assert "1/3" in body and "✓ n1 tool_1" in body

    def test_same_terminal_throttled_by_min_interval(self, card):
        card.card.start()
        card.card.plan_done(_plan())
        card.card.tick(_snap(success=1))
        assert card.im.update_card.call_count == 2
        # 终态数未变 + 间隔不足 → 不刷
        card.clock.advance(3.0)
        card.card.tick(_snap(success=1))
        assert card.im.update_card.call_count == 2
        # 间隔达标 → 刷新
        card.clock.advance(6.0)
        card.card.tick(_snap(success=1))
        assert card.im.update_card.call_count == 3

    def test_update_failure_circuit_breaks(self, card):
        card.card.start()
        card.card.plan_done(_plan())
        card.im.update_card.side_effect = RuntimeError("patch 429")
        card.card.tick(_snap(success=1))          # 触发失败 → 熔断
        card.card.tick(_snap(success=2))          # 终态再变也不再更
        card.card.finish("success", {"n1": "success"})
        # plan_done 1 次成功 + 熔断尝试 1 次 = 2 次
        assert card.im.update_card.call_count == 2

    def test_running_nodes_shown_with_elapsed(self, card):
        card.card.start()
        card.card.tick(_snap(running=[("n2 sc_scenic", 754.0)]))
        body = card.im.update_card.call_args.args[1]["elements"][-1][
            "text"]["content"]
        assert "运行中" in body and "n2 sc_scenic" in body
        assert "12m34s" in body


class TestFinish:
    def test_finish_renders_status_counts_elapsed(self, card):
        card.card.start()
        card.clock.advance(95.0)
        card.card.finish("partial_success",
                         {"n1": "success", "n2": "failed", "n3": "skipped"})
        upd = card.im.update_card.call_args.args[1]
        assert upd["header"] == "研究任务完成"
        body = upd["elements"][-1]["text"]["content"]
        assert "partial_success" in body
        assert "✓1" in body and "✗1" in body and "–1" in body
        assert "1m35s" in body

    def test_finish_error_renders_reason(self, card):
        card.card.start()
        card.card.finish_error("超过 3600s 超时终止")
        upd = card.im.update_card.call_args.args[1]
        assert upd["header"] == "研究任务异常终止"
        assert "超时终止" in upd["elements"][-1]["text"]["content"]

    def test_no_update_after_finish(self, card):
        card.card.start()
        card.card.finish("success", {"n1": "success"})
        n = card.im.update_card.call_count
        card.card.tick(_snap(success=1))
        assert card.im.update_card.call_count == n


class TestSnapshot:
    def _handle(self, node_id, state, started=None):
        return TaskHandle(
            execution_id=new_ulid(), task_id="t1", node_id=node_id,
            state=state, started_at=started or datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )

    def test_snapshot_categorizes_states(self):
        plan = _plan(4)
        started = datetime.now(UTC) - timedelta(seconds=130)
        scheduler = SimpleNamespace(plan=plan, _handles={
            "n1": self._handle("n1", ExecutionState.SUCCESS),
            "n2": self._handle("n2", ExecutionState.FAILED),
            "n3": self._handle("n3", ExecutionState.RUNNING, started),
            "n4": self._handle("n4", ExecutionState.DENIED),
        })
        snap = ResearchRunner._snapshot_nodes(scheduler)
        assert snap["counts"]["success"] == 1
        assert snap["counts"]["failed"] == 1
        assert snap["counts"]["denied"] == 1
        assert snap["counts"]["running"] == 1
        assert snap["total"] == 4
        # 运行中节点带工具名与 ≈130s 已耗时
        label, el = snap["running"][0]
        assert label == "n3 tool_3" and 120 <= el <= 140
        # done 序列带标记
        assert ("✓", "n1 tool_1") in snap["done"]
        assert ("✗", "n2 tool_2") in snap["done"]
        assert ("⊘", "n4 tool_4") in snap["done"]


class TestFmtElapsed:
    def test_seconds_minutes_hours(self):
        assert _fmt_elapsed(12) == "12s"
        assert _fmt_elapsed(308) == "5m08s"
        assert _fmt_elapsed(3720) == "1h02m"
        assert _fmt_elapsed(-5) == "0s"
