"""ApprovalBroker 测试：跨线程决策传递（Phase 14 板块②）。"""
import threading

from orchestrator.approval_broker import ApprovalBroker


def test_decide_before_wait_returns_immediately():
    """决策先于 wait 到达（发卡后秒点）：wait 不阻塞直接取走。"""
    b = ApprovalBroker()
    assert b.decide("dw1", "approve", "ou_1") is True
    assert b.wait("dw1", timeout=0.1) == "approve"


def test_wait_blocks_until_decide():
    """wait 阻塞，另一线程 decide 后唤醒。"""
    b = ApprovalBroker()
    t = threading.Timer(0.05, lambda: b.decide("dw1", "deny", "ou_1"))
    t.start()
    assert b.wait("dw1", timeout=2.0) == "deny"
    t.join()


def test_wait_timeout_returns_none():
    b = ApprovalBroker()
    assert b.wait("dw1", timeout=0.05) is None


def test_duplicate_decide_is_idempotent():
    """重复点击：第二次 decide 被拒，首个决策不被覆盖。"""
    b = ApprovalBroker()
    assert b.decide("dw1", "approve", "ou_1") is True
    assert b.decide("dw1", "deny", "ou_2") is False
    assert b.wait("dw1", timeout=0.1) == "approve"


def test_invalid_decision_rejected():
    b = ApprovalBroker()
    assert b.decide("dw1", "maybe", "ou_1") is False
    assert b.wait("dw1", timeout=0.05) is None


def test_wait_cleans_up_after_decision():
    """取走后条目清理：内部无残留，同 id 再 decide 是新决策。"""
    b = ApprovalBroker()
    b.decide("dw1", "approve", "ou_1")
    assert b.wait("dw1", timeout=0.1) == "approve"
    assert "dw1" not in b._decisions
    assert b._events == {}
    assert b.decide("dw1", "deny", "ou_2") is True  # 新决策（非重复）
