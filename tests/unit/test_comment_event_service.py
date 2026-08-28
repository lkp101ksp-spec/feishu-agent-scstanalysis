"""CommentEventService 单测：防循环 / 绑定过滤 / sync+notify 链路（ADR-0033）。"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from orchestrator.templates.comment_event_service import CommentEventService


def _svc(bot_open_id="ou_bot", bound_doc="doccnX", expired=False):
    """构造事件服务 + mock 依赖；expired 控制绑定是否过期。"""
    session_repo = MagicMock()
    session_repo.list_active.return_value = [
        MagicMock(bound_doc_id=bound_doc, owner_open_id="ou_owner",
                  source_chat_id="oc_1",
                  bind_expires_at=(
                      datetime.now(timezone.utc) - timedelta(seconds=1)
                      if expired else
                      datetime.now(timezone.utc) + timedelta(hours=1)),
                  )
    ]
    sync = MagicMock()
    sync.sync.return_value = {"fetched": 1, "new": 1, "updated": 0}
    notify = MagicMock()
    notify.notify_new_pending.return_value = {"notified": 1}
    svc = CommentEventService(
        session_repo=session_repo, sync_service=sync, notify_service=notify,
        bot_open_id=bot_open_id)
    return svc, sync, notify


def test_handle_syncs_and_notifies_for_bound_doc():
    """绑定 doc 的评论事件：触发 sync + notify owner。"""
    svc, sync, notify = _svc()
    out = svc.handle(file_token="doccnX", operator_open_id="ou_teacher")
    sync.sync.assert_called_once_with(doc_id="doccnX")
    notify.notify_new_pending.assert_called_once_with(
        doc_id="doccnX", owner_open_id="ou_owner", chat_id="oc_1")
    assert out["status"] == "handled"


def test_handle_ignores_bot_own_comments():
    """bot 自己的评论（回执写回触发）直接忽略，防死循环。"""
    svc, sync, notify = _svc(bot_open_id="ou_bot")
    out = svc.handle(file_token="doccnX", operator_open_id="ou_bot")
    sync.sync.assert_not_called()
    assert out["status"] == "ignored_bot_self"


def test_handle_ignores_unbound_doc():
    """未绑定文档的评论不处理。"""
    svc, sync, notify = _svc()
    out = svc.handle(file_token="doccnOTHER", operator_open_id="ou_t")
    sync.sync.assert_not_called()
    assert out["status"] == "ignored_unbound"


def test_handle_ignores_expired_binding():
    """绑定已过期的文档不处理。"""
    svc, sync, notify = _svc(expired=True)
    out = svc.handle(file_token="doccnX", operator_open_id="ou_t")
    sync.sync.assert_not_called()
    assert out["status"] == "ignored_unbound"


def test_handle_without_bot_open_id_skips_conservatively():
    """拿不到 bot open_id 时保守跳过（宁可漏处理也不冒死循环风险）。"""
    svc, sync, notify = _svc(bot_open_id=None)
    out = svc.handle(file_token="doccnX", operator_open_id="ou_t")
    sync.sync.assert_not_called()
    assert out["status"] == "skipped_no_bot_id"


def test_handle_sync_exception_isolated():
    """单事件异常吃掉返回 error 不抛出（保长连接）。"""
    svc, sync, notify = _svc()
    sync.sync.side_effect = RuntimeError("boom")
    out = svc.handle(file_token="doccnX", operator_open_id="ou_t")
    assert out["status"] == "error"


def test_handle_commits_session_on_success_and_rolls_back_on_error():
    """独立 event_session：成功后 commit，异常时 rollback（repo 只 flush）。"""
    session = MagicMock()
    svc, sync, notify = _svc()
    svc.session = session
    svc.handle(file_token="doccnX", operator_open_id="ou_t")
    session.commit.assert_called_once()

    session2 = MagicMock()
    svc2, sync2, _ = _svc()
    svc2.session = session2
    sync2.sync.side_effect = RuntimeError("boom")
    svc2.handle(file_token="doccnX", operator_open_id="ou_t")
    session2.rollback.assert_called_once()
    session2.commit.assert_not_called()


# --- bot_info：原始请求模式获取 bot open_id（缓存 + 失败 None） ---


def _fake_raw(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.raw.content = json.dumps(payload).encode("utf-8")
    return resp


def test_get_bot_open_id_caches_and_falls_back(monkeypatch):
    """拉取一次并缓存；失败返回 None。"""
    import feishu_adapter.bot_info as bi

    bi._CACHE.clear()  # 隔离其他用例的缓存污染
    sdk = MagicMock()
    sdk.request.return_value = _fake_raw(
        {"code": 0, "bot": {"open_id": "ou_bot_9"}})
    assert bi.get_bot_open_id(sdk) == "ou_bot_9"
    assert bi.get_bot_open_id(sdk) == "ou_bot_9"  # 缓存：只调一次 API
    sdk.request.assert_called_once()

    bi._CACHE.clear()
    bad = MagicMock()
    bad.request.side_effect = RuntimeError("net")
    assert bi.get_bot_open_id(bad) is None
    bi._CACHE.clear()
