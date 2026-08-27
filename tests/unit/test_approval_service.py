import hashlib
import hmac
from datetime import datetime, timedelta

from orchestrator.approval_service import (
    ApprovalPolicy,
    ApprovalService,
    _hmac_sign,
)


def test_policy_skips_approval_when_bound_to_doc():
    class FakeSession:
        bound_doc_id = "d1"
        bind_expires_at = datetime.utcnow() + timedelta(seconds=600)

    pol = ApprovalPolicy()
    assert pol.can_skip_approval("write_doc", {"doc_id": "d1"}, FakeSession()) is True
    assert pol.can_skip_approval("write_doc", {"doc_id": "d2"}, FakeSession()) is False
    assert (
        pol.can_skip_approval("write_base_projection", {"doc_id": "d1"}, FakeSession())
        is False
    )


def test_policy_rejects_when_bind_expired():
    class FakeSession:
        bound_doc_id = "d1"
        bind_expires_at = datetime.utcnow() - timedelta(seconds=10)

    pol = ApprovalPolicy()
    assert pol.can_skip_approval("write_doc", {"doc_id": "d1"}, FakeSession()) is False


def test_policy_no_bind_returns_false():
    class FakeSession:
        bound_doc_id = None
        bind_expires_at = None

    pol = ApprovalPolicy()
    assert pol.can_skip_approval("write_doc", {"doc_id": "d1"}, FakeSession()) is False


def test_hmac_sign_and_verify():
    secret = "abc"
    body = b'{"a": 1}'
    sig = _hmac_sign(body, secret)
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sig == expected


def test_request_sync_returns_true_when_skipped():
    svc = ApprovalService(im_adapter=None, approval_repo=None, audit_repo=None)

    class FakeSession:
        bound_doc_id = "d1"
        bind_expires_at = datetime.utcnow() + timedelta(seconds=600)

    assert (
        svc.request_sync(
            tool_name="write_doc",
            args_preview={"doc_id": "d1"},
            actor_open_id="ou_1",
            session=FakeSession(),
        )
        is True
    )
