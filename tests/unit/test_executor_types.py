from shared.errors import (
    ArtifactNotFoundError,
    FileTooLargeError,
    RenderError,
    SandboxUnavailableError,
    ToolBlockedError,
    ToolDeniedError,
)
from shared.executor_types import ExecutionState, ExecutionTask, TaskHandle


def test_execution_state_enum():
    assert ExecutionState.PENDING.value == "pending"
    assert ExecutionState.RUNNING.value == "running"
    assert ExecutionState.SUCCESS.value == "success"
    assert ExecutionState.FAILED.value == "failed"
    assert ExecutionState.SKIPPED.value == "skipped"
    assert ExecutionState.CANCELLED.value == "cancelled"
    assert ExecutionState.DENIED.value == "denied"


def test_execution_task_minimal():
    task = ExecutionTask(
        task_id="t1",
        node_id="n1",
        tool_name="run_python",
        inputs={"code": "print(1)", "session_id": "s1"},
    )
    assert task.risk_level == "L1_compute"  # default
    assert task.timeout_sec == 60  # default


def test_errors_distinct():
    assert ToolBlockedError("x").code == "TOOL_BLOCKED"
    assert ToolDeniedError("x").code == "TOOL_DENIED"
    assert SandboxUnavailableError("x").code == "SANDBOX_UNAVAILABLE"
    assert FileTooLargeError("x").code == "FILE_TOO_LARGE"
    assert RenderError("x").code == "RENDER_ERROR"
    assert ArtifactNotFoundError("x").code == "ARTIFACT_NOT_FOUND"