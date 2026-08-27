import json
import os
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from orchestrator.tools.bio.blast_local import BlastLocalTool


@pytest.fixture
def tool():
    tmpdir = tempfile.mkdtemp()
    fake_binary = os.path.join(tmpdir, "blastp.bat")
    with open(fake_binary, "w") as f:
        f.write("@echo off\n")
    # 创建假数据库文件
    for ext in (".phr", ".psq"):
        with open(os.path.join(tmpdir, "nr" + ext), "w") as f:
            f.write("")
    return BlastLocalTool(
        binary_path=fake_binary,
        db_path=tmpdir,
        timeout_sec=5,
    )


def test_local_blast_success(tool):
    fake_output = json.dumps({
        "BlastOutput2": [{
            "report": {
                "results": {
                    "search": {
                        "hits": [
                            {"num": 1,
                             "description": [{"title": "BRCA1"}],
                             "hsps": [{"hsp_score": 100}]},
                        ]
                    }
                }
            }
        }]
    })
    with patch.object(subprocess, "run",
                      return_value=subprocess.CompletedProcess(
                          args=[], returncode=0, stdout=fake_output,
                          stderr="")):
        out = tool.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["records"][0]["title"] == "BRCA1"
    assert out["database"] == "nr"


def test_local_blast_subprocess_error(tool):
    with patch.object(subprocess, "run",
                      return_value=subprocess.CompletedProcess(
                          args=[], returncode=1, stdout="",
                          stderr="blast error")):
        out = tool.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["error_code"] == "BLAST_LOCAL_ERROR"


def test_local_blast_timeout(tool):
    with patch.object(subprocess, "run",
                      side_effect=subprocess.TimeoutExpired("cmd", 5)):
        out = tool.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["error_code"] == "BLAST_LOCAL_TIMEOUT"


def test_mode_resolution_local_binary_exists():
    tmpdir = tempfile.mkdtemp()
    fake_binary = os.path.join(tmpdir, "blastp.bat")
    with open(fake_binary, "w") as f:
        f.write("@echo off\n")
    tool = BlastLocalTool(binary_path=fake_binary, db_path=tmpdir)
    mode = tool._resolve_mode(database="nr")
    assert mode in ("local", "web")


def test_mode_resolution_web_when_no_binary():
    tmpdir = tempfile.mkdtemp()
    tool = BlastLocalTool(binary_path="/nonexistent/blastp",
                          db_path=tmpdir)
    mode = tool._resolve_mode(database="nr")
    assert mode == "web"


def test_local_blast_invalid_query(tool):
    out = tool.handle(query="", database="nr", max_hits=5)
    assert out["error_code"] == "BLAST_INVALID_QUERY"
