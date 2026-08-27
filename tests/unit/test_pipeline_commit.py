"""run_im_pipeline 主 session 提交语义：成功 commit、异常 rollback（联调修复）。"""
import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from gateway.app import create_app, run_im_pipeline
from persistence.models import Base
from shared.errors import FeishuAgentError


def _make_app(process_side_effect=None):
    """构造带内存 sqlite 幂等表 + mock orchestrator + mock 主 session 的 app。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    orch = MagicMock()
    if process_side_effect is not None:
        orch.process.side_effect = process_side_effect
    else:
        orch.process.return_value = {"status": "success", "task_id": None}

    app = create_app(secret="s", orchestrator=orch, session_factory=factory)
    app.state.main_session = MagicMock()
    return app


def _payload() -> dict:
    return {
        "header": {"event_id": "e1", "event_type": "im.message.receive_v1",
                   "app_id": "cli_x"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_x"}, "sender_type": "user"},
            "message": {
                "chat_id": "oc_x", "message_id": "om_x",
                "message_type": "text", "chat_type": "p2p",
                "content": json.dumps({"text": "hi"}),
            },
        },
    }


def test_pipeline_commits_main_session_on_success():
    app = _make_app()
    result = run_im_pipeline(app, "cli_x", _payload())
    assert result["status"] == "success"
    app.state.main_session.commit.assert_called_once()
    app.state.main_session.rollback.assert_not_called()


def test_pipeline_rolls_back_main_session_on_error():
    app = _make_app(process_side_effect=FeishuAgentError("boom"))
    with pytest.raises(FeishuAgentError):
        run_im_pipeline(app, "cli_x", _payload())
    app.state.main_session.rollback.assert_called_once()
    app.state.main_session.commit.assert_not_called()


def test_pipeline_without_main_session_still_works():
    """未挂载 main_session 时（纯 webhook 组装）不 commit 也不报错。"""
    app = _make_app()
    app.state.main_session = None
    result = run_im_pipeline(app, "cli_x", _payload())
    assert result["status"] == "success"
