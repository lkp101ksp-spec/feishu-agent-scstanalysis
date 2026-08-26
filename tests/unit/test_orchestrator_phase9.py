"""Phase 9 T8: Orchestrator process_phase9 冒烟测试。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from orchestrator.app import Orchestrator


def _orch():
    orch = Orchestrator(
        llm_router=MagicMock(), session_service=MagicMock(),
        task_service=MagicMock(), bind_doc_service=MagicMock(),
        doc_write_service=MagicMock(), im_adapter=MagicMock(),
        settings=MagicMock(),
    )
    orch.planner = MagicMock()  # 通过 phase 门检查
    return orch


def _incoming(text):
    return SimpleNamespace(
        text=text, chat_id="oc_1", sender_open_id="ou_1",
    )


def test_template_find_command_parses_tag():
    orch = _orch()
    us = MagicMock()
    us.search.return_value = [{
        "template_id": "t1", "name": "blast 流程",
        "score": 2.5, "tags": ["bio"], "favorite_count": 3,
    }]
    us.render.return_value = "检索结果（1 条）：\n- blast 流程 (score=2.5, ❤3, #bio)"
    orch.unified_search_service = us

    out = orch.process_phase9(_incoming("/template-find blast #BIO"))

    us.search.assert_called_once_with(query="blast", tag="bio", limit=10)
    assert out == {"status": "find_rendered", "count": 1}
    orch.im.reply.assert_called_once()


def test_orchestrator_phase9_has_process_phase9():
    assert hasattr(_orch(), "process_phase9")
