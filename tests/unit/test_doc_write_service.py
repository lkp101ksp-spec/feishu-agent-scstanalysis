"""DocWriteService 测试：bind-doc 授权窗口内的文档写入。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from feishu_adapter.doc_adapter import DocAdapter
from orchestrator.doc_write_service import DocWriteService
from persistence.repositories.doc_write_repo import DocWriteRepo
from persistence.repositories.session_repo import SessionRepo
from shared.errors import DocWriteError


def _make_service(
    bound_doc_id: str | None = "doccnABC123",
    expires_in_sec: int = 600,
    append_returns: str = "blk_x",
    append_raises: Exception | None = None,
) -> tuple[DocWriteService, MagicMock, MagicMock, MagicMock]:
    session_repo = MagicMock(spec=SessionRepo)
    if bound_doc_id is None:
        session_repo.get.return_value = None
    else:
        session_repo.get.return_value = MagicMock(
            bound_doc_id=bound_doc_id,
            bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in_sec),
        )

    doc_repo = MagicMock(spec=DocWriteRepo)

    doc_adapter = MagicMock(spec=DocAdapter)
    if append_raises is not None:
        doc_adapter.append_plain_text.side_effect = append_raises
    else:
        doc_adapter.append_plain_text.return_value = append_returns

    service = DocWriteService(
        session_repo=session_repo, doc_repo=doc_repo, doc_adapter=doc_adapter
    )
    return service, session_repo, doc_repo, doc_adapter


def test_write_to_bound_doc_success():
    svc, _, doc_repo, doc_adapter = _make_service()
    result = svc.write_plain_text(
        session_id="s1", task_id="t1", requested_by="ou_x", text="hello"
    )
    assert result["status"] == "success"
    assert result["doc_id"] == "doccnABC123"
    assert result["anchor_block_id"] == "blk_x"
    doc_repo.create_pending.assert_called_once()
    mark_success_kwargs = doc_repo.mark_success.call_args.kwargs
    assert mark_success_kwargs["anchor_block_id"] == "blk_x"
    doc_adapter.append_plain_text.assert_called_once_with(
        doc_id="doccnABC123", text="hello"
    )


def test_write_without_session_raises():
    svc, _, _, _ = _make_service(bound_doc_id=None)
    with pytest.raises(DocWriteError) as exc:
        svc.write_plain_text(session_id="s1", task_id="t1", requested_by="ou_x", text="hi")
    assert "no valid bind" in str(exc.value).lower()


def test_write_with_expired_bind_raises():
    svc, _, _, _ = _make_service(expires_in_sec=-60)
    with pytest.raises(DocWriteError):
        svc.write_plain_text(session_id="s1", task_id="t1", requested_by="ou_x", text="hi")


def test_write_records_failure_when_adapter_throws():
    svc, _, doc_repo, _ = _make_service(append_raises=RuntimeError("lark-cli boom"))
    with pytest.raises(DocWriteError) as exc:
        svc.write_plain_text(session_id="s1", task_id="t1", requested_by="ou_x", text="hi")
    assert "lark-cli boom" in str(exc.value)
    doc_repo.mark_failed.assert_called_once()
    fail_kwargs = doc_repo.mark_failed.call_args.kwargs
    assert "RuntimeError" in fail_kwargs["reason"]


def test_write_transitions_through_full_state_machine():
    svc, _, doc_repo, _ = _make_service()
    svc.write_plain_text(session_id="s1", task_id="t1", requested_by="ou_x", text="x")
    # 调用顺序：create_pending → transition(approved) → transition(writing) → mark_success
    assert doc_repo.create_pending.call_count == 1
    transitions = [c.args[1] for c in doc_repo.transition.call_args_list]
    assert transitions == ["approved", "writing"]
    assert doc_repo.mark_success.call_count == 1