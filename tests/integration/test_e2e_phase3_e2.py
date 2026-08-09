"""E2: while 循环（max_iterations 上限）。"""
import pytest

from orchestrator.runtime.plan_runtime import PlanRuntime
from shared.errors import LoopMaxIterError


def test_e2_while_loop_iteration_increments():
    rt = PlanRuntime(state_repo=None, audit_repo=None, max_iterations=3)
    assert rt.loop_iteration_done("loop_1") == 1
    assert rt.loop_iteration_done("loop_1") == 2
    assert rt.loop_iteration_done("loop_1") == 3
    with pytest.raises(LoopMaxIterError):
        rt.loop_iteration_done("loop_1")


def test_e2_while_max_iter_2():
    rt = PlanRuntime(state_repo=None, audit_repo=None, max_iterations=2)
    rt.loop_iteration_done("loop_1")
    rt.loop_iteration_done("loop_1")
    with pytest.raises(LoopMaxIterError):
        rt.loop_iteration_done("loop_1")