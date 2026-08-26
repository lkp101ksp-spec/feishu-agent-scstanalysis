"""Phase 8 T9: Orchestrator process_phase8 冒烟测试。"""
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


def test_orchestrator_phase8_favorites_command():
    orch = _orch()
    fav = MagicMock()
    tpl = MagicMock(spec=["template_id"])
    tpl.template_id = "t1"
    fav.list_favorites.return_value = [tpl]
    orch.favorite_service = fav

    out = orch.process_phase8(_incoming("/template-favorites"))

    assert out == {"status": "favorites_listed", "templates": ["t1"]}
    fav.list_favorites.assert_called_once_with("ou_1")
    orch.im.reply.assert_called_once_with("oc_1", "我的收藏: t1")


def test_orchestrator_phase8_has_process_phase8():
    assert hasattr(_orch(), "process_phase8")
