"""lark-cli 调用封装测试。"""
import subprocess
from unittest.mock import patch

import pytest

from feishu_adapter.client import LarkCLI, LarkCLIError


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_success_parses_json_stdout():
    fake = _proc(returncode=0, stdout='{"ok": true, "id": "x"}')
    with patch("subprocess.run", return_value=fake) as mock_run:
        client = LarkCLI()
        result = client.run(["test"])
    assert result == {"ok": True, "id": "x"}
    mock_run.assert_called_once()


def test_run_success_empty_stdout_returns_empty_dict():
    fake = _proc(returncode=0, stdout="")
    with patch("subprocess.run", return_value=fake):
        assert LarkCLI().run(["test"]) == {}


def test_run_nonzero_raises_with_stderr_in_message():
    fake = _proc(returncode=1, stderr="boom message")
    with patch("subprocess.run", return_value=fake):
        with pytest.raises(LarkCLIError) as exc:
            LarkCLI().run(["test"])
    assert "boom message" in str(exc.value)
    assert "exit=1" in str(exc.value)


def test_run_timeout_raises_lark_cli_error():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="lark-cli", timeout=10)):
        with pytest.raises(LarkCLIError) as exc:
            LarkCLI(timeout=10).run(["test"])
    assert "timeout" in str(exc.value).lower()


def test_run_binary_not_found_raises_lark_cli_error():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(LarkCLIError) as exc:
            LarkCLI().run(["test"])
    assert "not found" in str(exc.value).lower()


def test_run_non_json_output_raises_lark_cli_error():
    fake = _proc(returncode=0, stdout="not json at all")
    with patch("subprocess.run", return_value=fake):
        with pytest.raises(LarkCLIError) as exc:
            LarkCLI().run(["test"])
    assert "non-json" in str(exc.value).lower()
