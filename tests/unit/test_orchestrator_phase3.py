from unittest.mock import MagicMock

from orchestrator.app import Orchestrator


def test_phase3_orchestrator_has_process_phase3():
    orch = Orchestrator(
        llm_router=MagicMock(), session_service=MagicMock(),
        task_service=MagicMock(), bind_doc_service=MagicMock(),
        doc_write_service=MagicMock(), im_adapter=MagicMock(),
        settings=MagicMock(),
    )
    assert hasattr(orch, "process_phase3")