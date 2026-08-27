"""E6-E8: bind_doc 续期 / 续期卡片 / DAGValidationError on dynamic append。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from orchestrator.bind_doc_service import BindDocService
from orchestrator.planner.dag_schema import DAGNode
from orchestrator.runtime.plan_runtime import PlanRuntime
from shared.errors import BindDocInvalidError, DAGValidationError, DynamicAppendError


class FakeSession:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_e6_renew_extends_expires_at():
    fake_session_repo = MagicMock()
    fake_session_repo.get.return_value = FakeSession(
        session_id="s1", bound_doc_id="d1",
        bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
    )
    svc = BindDocService(
        session_service=MagicMock(), audit_repo=MagicMock(),
        ttl_sec=1800, session_repo=fake_session_repo,
    )
    new_exp = svc.renew(session_id="s1")
    assert new_exp > datetime.now(timezone.utc) + timedelta(seconds=1700)


def test_e6_renew_no_bind_raises():
    fake_session_repo = MagicMock()
    fake_session_repo.get.return_value = FakeSession(
        session_id="s1", bound_doc_id=None, bind_expires_at=None,
    )
    svc = BindDocService(
        session_service=MagicMock(), audit_repo=MagicMock(),
        ttl_sec=1800, session_repo=fake_session_repo,
    )
    with pytest.raises(BindDocInvalidError):
        svc.renew(session_id="s1")


def test_e7_renew_card_threshold():
    fake_im = MagicMock()
    fake_session_repo = MagicMock()
    fake_session_repo.list_active.return_value = [
        FakeSession(session_id="s1", bound_doc_id="d1",
                    bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=100),
                    source_chat_id="chat_1"),
    ]
    svc = BindDocService(
        session_service=MagicMock(), audit_repo=MagicMock(),
        ttl_sec=1800, session_repo=fake_session_repo,
        im_adapter=fake_im, renew_threshold_sec=300,
    )
    import asyncio
    asyncio.run(svc.maybe_send_renew_card())
    fake_im.send_card.assert_called_once()


def test_e8_dynamic_append_cyclic_fails():
    rt = PlanRuntime(state_repo=None, audit_repo=None)
    sub1 = DAGNode(node_id="sub1", kind="tool", tool_name="a",
                   inputs={}, depends_on=["sub2"])
    sub2 = DAGNode(node_id="sub2", kind="tool", tool_name="b",
                   inputs={}, depends_on=["sub1"])
    with pytest.raises((DynamicAppendError, DAGValidationError)):
        rt.append_dynamic_nodes(plan_id="p", parent_node_id="p1",
                                 new_nodes=[sub1, sub2])
