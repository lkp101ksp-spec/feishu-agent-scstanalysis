"""Phase 78 P2：research 失败终态自动 code 回退环单测。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from orchestrator.research_runner import ResearchRunner


def _mk_runner(status: str, final_text: str = ""):
    orch = SimpleNamespace(settings=SimpleNamespace(report_folder_token=""))
    coding = MagicMock()
    coding.run_sync.return_value = {"status": "final", "steps": 2,
                                    "final_text": final_text}
    orch.coding_runner = coding
    im = MagicMock()
    runner = ResearchRunner.__new__(ResearchRunner)
    runner.orch = orch
    runner.im = im
    return runner, coding, im


class TestMaybeCodeFallback:
    def test_success_no_fallback(self):
        runner, coding, _ = _mk_runner("success")
        runner._maybe_code_fallback(MagicMock(), "任务", {"status": "success"})
        coding.run_sync.assert_not_called()

    def test_failed_triggers_fallback_and_retry_on_marker(self):
        runner, coding, im = _mk_runner("failed", final_text="修好了\n[RETRYABLE]")
        runner._run = MagicMock()
        incoming = MagicMock()
        out = {"status": "failed", "failures": ["- n2 SCRIPT_ERROR: boom"]}
        runner._maybe_code_fallback(incoming, "分析X", out)
        brief = coding.run_sync.call_args[0][1]
        assert "分析X" in brief and "SCRIPT_ERROR" in brief
        runner._run.assert_called_once()
        assert runner._run.call_args[1].get("_retried") is True or \
            runner._run.call_args[0][3] is True

    def test_not_retryable_no_rerun(self):
        runner, coding, _ = _mk_runner("failed", final_text="数据缺失 [NOT_RETRYABLE]")
        runner._run = MagicMock()
        runner._maybe_code_fallback(MagicMock(), "分析X",
                                    {"status": "failed", "failures": []})
        coding.run_sync.assert_called_once()
        runner._run.assert_not_called()

    def test_coding_crash_swallowed(self):
        runner, coding, im = _mk_runner("failed")
        coding.run_sync.side_effect = RuntimeError("boom")
        runner._run = MagicMock()
        runner._maybe_code_fallback(MagicMock(), "分析X",
                                    {"status": "failed", "failures": []})
        runner._run.assert_not_called()

    def test_partial_failure_triggers(self):
        runner, coding, _ = _mk_runner("success_with_partial_failure",
                                       final_text="[NOT_RETRYABLE]")
        runner._run = MagicMock()
        runner._maybe_code_fallback(MagicMock(), "分析X",
                                    {"status": "success_with_partial_failure",
                                     "failures": ["- n1 X: y"]})
        coding.run_sync.assert_called_once()
