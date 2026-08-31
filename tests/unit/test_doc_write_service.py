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
    bind_anchor: str | None = None,
    root_children: list | None = None,
    last_anchor: str | None = None,
) -> tuple[DocWriteService, MagicMock, MagicMock, MagicMock]:
    session_repo = MagicMock(spec=SessionRepo)
    if bound_doc_id is None:
        session_repo.get.return_value = None
    else:
        session_repo.get.return_value = MagicMock(
            bound_doc_id=bound_doc_id,
            bind_anchor=bind_anchor,
            bind_expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in_sec),
        )

    doc_repo = MagicMock(spec=DocWriteRepo)
    doc_repo.latest_success_anchor_for_session.return_value = last_anchor

    doc_adapter = MagicMock(spec=DocAdapter)
    if append_raises is not None:
        doc_adapter.append_plain_text.side_effect = append_raises
    else:
        doc_adapter.append_plain_text.return_value = append_returns
    doc_adapter.list_root_children.return_value = root_children or []

    service = DocWriteService(
        session_repo=session_repo, doc_repo=doc_repo, doc_adapter=doc_adapter
    )
    return service, session_repo, doc_repo, doc_adapter


def _text_block(block_id: str, text: str, key: str = "text") -> dict:
    return {"block_id": block_id, "block_type": 2,
            key: {"elements": [{"text_run": {"content": text}}]}}


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
        doc_id="doccnABC123", text="hello", index=-1
    )


def test_write_with_anchor_inserts_after_matched_block():
    """绑定带锚点：插到第一个文字包含锚点的块之后。"""
    children = [
        _text_block("b0", "引言"),
        _text_block("b1", "1 测试", key="heading2"),
        _text_block("b2", "其他内容"),
    ]
    svc, _, _, doc_adapter = _make_service(
        bind_anchor="1 测试", root_children=children)
    svc.write_plain_text(session_id="s1", task_id="t1",
                         requested_by="ou_x", text="hi")
    doc_adapter.append_plain_text.assert_called_once_with(
        doc_id="doccnABC123", text="hi", index=2)


def test_write_with_anchor_follows_last_write():
    """有历史成功写入：插到上次写入块之后，保证顺序向下。"""
    children = [
        _text_block("b0", "1 测试"),
        _text_block("b_last", "上次写入"),
        _text_block("b2", "其他"),
    ]
    svc, _, _, doc_adapter = _make_service(
        bind_anchor="1 测试", root_children=children, last_anchor="b_last")
    svc.write_plain_text(session_id="s1", task_id="t1",
                         requested_by="ou_x", text="hi")
    doc_adapter.append_plain_text.assert_called_once_with(
        doc_id="doccnABC123", text="hi", index=2)


def test_write_with_anchor_not_found_falls_back_to_end():
    """锚点找不到：回退末尾追加（index=-1）。"""
    svc, _, _, doc_adapter = _make_service(
        bind_anchor="不存在的章节", root_children=[_text_block("b0", "引言")])
    svc.write_plain_text(session_id="s1", task_id="t1",
                         requested_by="ou_x", text="hi")
    doc_adapter.append_plain_text.assert_called_once_with(
        doc_id="doccnABC123", text="hi", index=-1)


def test_write_msg_anchor_overrides_session_anchor():
    """消息级锚点（#写到）优先于会话级 bind_anchor 定位。"""
    children = [
        _text_block("b0", "旧章节"),
        _text_block("b1", "新章节"),
        _text_block("b2", "其他"),
    ]
    svc, _, doc_repo, doc_adapter = _make_service(
        bind_anchor="旧章节", root_children=children)
    svc.write_plain_text(session_id="s1", task_id="t1", requested_by="ou_x",
                         text="hi", anchor_text="新章节")
    doc_adapter.append_plain_text.assert_called_once_with(
        doc_id="doccnABC123", text="hi", index=2)
    # 续写跟随按同锚点过滤查询
    follow_kwargs = doc_repo.latest_success_anchor_for_session.call_args.kwargs
    assert follow_kwargs == {"anchor_text": "新章节"}
    # 写入记录也落 anchor_text，供下次同锚点跟随
    assert doc_repo.create_pending.call_args.kwargs["anchor_text"] == "新章节"


def test_write_msg_anchor_follows_same_anchor_history():
    """同锚点有历史成功写入：插到上次写入块之后。"""
    children = [
        _text_block("b0", "1 测试"),
        _text_block("b_last", "上次写入"),
    ]
    svc, _, _, doc_adapter = _make_service(
        root_children=children, last_anchor="b_last")
    svc.write_plain_text(session_id="s1", task_id="t1", requested_by="ou_x",
                         text="hi", anchor_text="1 测试")
    doc_adapter.append_plain_text.assert_called_once_with(
        doc_id="doccnABC123", text="hi", index=2)


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


# === Phase 14：card_confirm 卡片确认写回 ===

def _make_confirm_service(
    render_returns: str = "blk_y",
    render_raises: Exception | None = None,
    row_missing: bool = False,
):
    """card_confirm 用例的 service 工厂：doc_repo.get 返回带 doc_id 的 row。"""
    svc, _, doc_repo, doc_adapter = _make_service()
    doc_adapter.render_blocks.return_value = render_returns
    if render_raises is not None:
        doc_adapter.render_blocks.side_effect = render_raises
    if row_missing:
        doc_repo.get.return_value = None
    else:
        doc_repo.get.return_value = MagicMock(doc_id="doccnABC123")
    return svc, doc_repo, doc_adapter


def test_create_confirm_pending_records_card_confirm_mode():
    svc, doc_repo, _ = _make_confirm_service()
    out = svc.create_confirm_pending(
        session_id="s1", task_id="t1", requested_by="ou_x",
        preview_text="预览文本")
    assert out["doc_id"] == "doccnABC123"
    kwargs = doc_repo.create_pending.call_args.kwargs
    assert kwargs["approval_mode"] == "card_confirm"
    assert kwargs["payload_text"] == "预览文本"


def test_create_confirm_pending_rejects_expired_bind():
    svc, _, _, _ = _make_service(expires_in_sec=-60)
    with pytest.raises(DocWriteError):
        svc.create_confirm_pending(
            session_id="s1", task_id="t1", requested_by="ou_x",
            preview_text="x")


def test_complete_confirmed_approve_renders_and_succeeds():
    svc, doc_repo, doc_adapter = _make_confirm_service()
    out = svc.complete_confirmed(
        doc_write_id="dw1", decision="approve", blocks=["b1"])
    assert out["status"] == "success"
    assert out["anchor_block_id"] == "blk_y"
    transitions = [c.args[1] for c in doc_repo.transition.call_args_list]
    assert transitions == ["approved", "writing"]
    doc_adapter.render_blocks.assert_called_once_with("doccnABC123", ["b1"])
    assert doc_repo.mark_success.call_count == 1


def test_complete_confirmed_deny_cancels_without_render():
    svc, doc_repo, doc_adapter = _make_confirm_service()
    out = svc.complete_confirmed(
        doc_write_id="dw1", decision="deny", blocks=["b1"])
    assert out["status"] == "cancelled"
    doc_repo.transition.assert_called_once_with("dw1", "cancelled")
    doc_adapter.render_blocks.assert_not_called()


def test_complete_confirmed_timeout_cancels():
    svc, doc_repo, _ = _make_confirm_service()
    out = svc.complete_confirmed(
        doc_write_id="dw1", decision="timeout", blocks=["b1"])
    assert out["status"] == "cancelled"


def test_complete_confirmed_render_failure_marks_failed_not_raises():
    svc, doc_repo, _ = _make_confirm_service(
        render_raises=RuntimeError("lark boom"))
    out = svc.complete_confirmed(
        doc_write_id="dw1", decision="approve", blocks=["b1"])
    assert out["status"] == "failed"
    assert "lark boom" in out["reason"]
    doc_repo.mark_failed.assert_called_once()


def test_complete_confirmed_missing_row_fails_gracefully():
    svc, _, doc_adapter = _make_confirm_service(row_missing=True)
    out = svc.complete_confirmed(
        doc_write_id="dw1", decision="approve", blocks=["b1"])
    assert out["status"] == "failed"
    doc_adapter.render_blocks.assert_not_called()


# === Phase 17：node_l2 节点级审批 ===

def test_create_confirm_pending_node_l2_mode():
    """approval_mode="node_l2" 透传落库（write_doc 节点审批记录）。"""
    svc, doc_repo, _ = _make_confirm_service()
    out = svc.create_confirm_pending(
        session_id="s1", task_id="t1", requested_by="ou_x",
        preview_text="doc_id=doccnABC123; blocks: ...",
        approval_mode="node_l2")
    assert out["doc_id"] == "doccnABC123"
    kwargs = doc_repo.create_pending.call_args.kwargs
    assert kwargs["approval_mode"] == "node_l2"
    assert kwargs["payload_text"] == "doc_id=doccnABC123; blocks: ..."


def test_cancel_stale_pending_covers_both_modes():
    """启动清扫同时清 card_confirm 与 node_l2 的孤儿 pending。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from persistence.models import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        repo = DocWriteRepo(s)
        repo.create_pending(
            doc_write_id="dw_c", task_id="t1", doc_id="doc1",
            requested_by="ou_1", approval_mode="card_confirm",
            payload_text="x")
        repo.create_pending(
            doc_write_id="dw_n", task_id="t1", doc_id="doc1",
            requested_by="ou_1", approval_mode="node_l2",
            payload_text="y")
        repo.create_pending(
            doc_write_id="dw_b", task_id="t1", doc_id="doc1",
            requested_by="ou_1", approval_mode="bind_scope",
            payload_text="z")
        s.commit()

        svc = DocWriteService(
            session_repo=MagicMock(spec=SessionRepo), doc_repo=repo,
            doc_adapter=MagicMock(spec=DocAdapter),
        )
        assert svc.cancel_stale_pending() == 2
        s.commit()
        from persistence.models import DocWriteRow
        statuses = {
            row.doc_write_id: row.status
            for row in s.query(DocWriteRow).all()
        }
        assert statuses["dw_c"] == "cancelled"
        assert statuses["dw_n"] == "cancelled"
        assert statuses["dw_b"] == "pending"  # bind_scope 不在清扫范围
    finally:
        s.close()
        engine.dispose()
