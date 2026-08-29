"""Phase 9 T4: CommentNotifyService 单元测试（SQLite 真 repo + mock IM）。"""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator.templates.notify_service import CommentNotifyService
from persistence.models import Base
from persistence.repositories.comment_notify_repo import CommentNotifyRepo
from persistence.repositories.comment_repo import CommentRepo


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def env(session):
    im = MagicMock()
    svc = CommentNotifyService(
        CommentRepo(session), CommentNotifyRepo(session), im,
    )
    return svc, im, CommentRepo(session)


@pytest.fixture
def env_all(session):
    """notify_all=True 模式：全量评论提醒开关。"""
    im = MagicMock()
    svc = CommentNotifyService(
        CommentRepo(session), CommentNotifyRepo(session), im,
        notify_all=True,
    )
    return svc, im, CommentRepo(session)


def _comment(repo, cid, text):
    repo.upsert_one(comment_id=cid, doc_id="d1", user_name="导师", text=text)


def test_notify_new_pending_sends_im(env):
    svc, im, repo = env
    _comment(repo, "c1", "/replan t1 增加对照组")
    out = svc.notify_new_pending(
        doc_id="d1", owner_open_id="ou_o", chat_id="oc_1")
    assert out == {"notified": 1}
    im.reply.assert_called_once()
    msg = im.reply.call_args.args[1]
    assert "/comment-apply d1" in msg
    assert "导师" in msg


def test_dedup_no_resend(env):
    svc, im, repo = env
    _comment(repo, "c1", "/replan t1 备注")
    svc.notify_new_pending(doc_id="d1", owner_open_id="ou_o", chat_id="oc_1")
    out = svc.notify_new_pending(doc_id="d1", owner_open_id="ou_o", chat_id="oc_1")
    assert out == {"notified": 0}
    assert im.reply.call_count == 1


def test_skips_plain_comments(env):
    svc, im, repo = env
    _comment(repo, "c1", "写得不错")
    out = svc.notify_new_pending(doc_id="d1", owner_open_id="ou_o", chat_id="oc_1")
    assert out == {"notified": 0}
    im.reply.assert_not_called()


def test_skips_processed(env):
    svc, im, repo = env
    _comment(repo, "c1", "/replan t1 已处理过的")
    repo.mark_processed("c1")
    out = svc.notify_new_pending(doc_id="d1", owner_open_id="ou_o", chat_id="oc_1")
    assert out == {"notified": 0}
    im.reply.assert_not_called()


def test_empty_no_message(env):
    svc, im, _ = env
    out = svc.notify_new_pending(doc_id="d1", owner_open_id="ou_o", chat_id="oc_1")
    assert out == {"notified": 0}
    im.reply.assert_not_called()


def test_notify_all_pushes_plain_comments(env_all):
    """notify_all=True：纯闲聊评论也推送，标题为[评论提醒]，无 apply 提示。"""
    svc, im, repo = env_all
    _comment(repo, "c1", "写得不错")
    out = svc.notify_new_pending(doc_id="d1", owner_open_id="ou_o", chat_id="oc_1")
    assert out == {"notified": 1}
    im.reply.assert_called_once()
    msg = im.reply.call_args.args[1]
    assert "[评论提醒]" in msg
    assert "写得不错" in msg
    assert "/comment-apply" not in msg


def test_notify_all_mixed_keeps_apply_hint(env_all):
    """notify_all=True：指令+闲聊混合时全推，保留 apply 提示。"""
    svc, im, repo = env_all
    _comment(repo, "c1", "写得不错")
    _comment(repo, "c2", "/replan t1 增加对照组")
    out = svc.notify_new_pending(doc_id="d1", owner_open_id="ou_o", chat_id="oc_1")
    assert out == {"notified": 2}
    msg = im.reply.call_args.args[1]
    assert "写得不错" in msg
    assert "/comment-apply d1" in msg


def test_notify_all_dedup_no_resend(env_all):
    """notify_all=True：去重同样生效。"""
    svc, im, repo = env_all
    _comment(repo, "c1", "写得不错")
    svc.notify_new_pending(doc_id="d1", owner_open_id="ou_o", chat_id="oc_1")
    out = svc.notify_new_pending(doc_id="d1", owner_open_id="ou_o", chat_id="oc_1")
    assert out == {"notified": 0}
    assert im.reply.call_count == 1
