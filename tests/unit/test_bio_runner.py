"""Phase 20：BioRunner 单测（路径白名单 / dataset_id / 容器执行错误链）。

docker subprocess 全部 mock，不起真实容器。
"""
import json
import subprocess

import pytest

from orchestrator.tools.bio.bio_runner import (
    BioRunError,
    BioRunner,
    compute_dataset_id,
)


@pytest.fixture
def roots(tmp_path):
    """构造白名单根目录 + 目录内数据文件。"""
    data_root = tmp_path / "data"
    data_root.mkdir()
    f = data_root / "pbmc.h5ad"
    f.write_bytes(b"fake-h5ad")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    return {"root": data_root, "file": f, "outside": outside}


def _runner(roots, tmp_path):
    return BioRunner(
        image="bio:test", workspace_root=str(tmp_path / "ws"),
        data_roots=[str(roots["root"])],
    )


# === 路径白名单（spec §5） ===

def test_resolve_data_path_allowed(roots, tmp_path):
    """白名单内路径：返回 (挂载根, 相对路径, 主机绝对路径)。"""
    r = _runner(roots, tmp_path)
    mount_root, rel, host = r.resolve_data_path(str(roots["file"]))
    assert mount_root == str(roots["root"])
    assert rel == "pbmc.h5ad"
    assert host == str(roots["file"])


def test_resolve_data_path_forbidden(roots, tmp_path):
    """白名单外路径：SC_PATH_FORBIDDEN。"""
    r = _runner(roots, tmp_path)
    with pytest.raises(BioRunError) as ei:
        r.resolve_data_path(str(roots["outside"] / "x.h5ad"))
    assert ei.value.error_code == "SC_PATH_FORBIDDEN"


def test_resolve_data_path_empty(roots, tmp_path):
    """空路径：INVALID_INPUT。"""
    r = _runner(roots, tmp_path)
    with pytest.raises(BioRunError) as ei:
        r.resolve_data_path("   ")
    assert ei.value.error_code == "INVALID_INPUT"


def test_resolve_data_path_traversal_forbidden(roots, tmp_path):
    """../ 逃逸出白名单根：relative_to 失败 → SC_PATH_FORBIDDEN。"""
    r = _runner(roots, tmp_path)
    escape = str(roots["root"] / ".." / "elsewhere" / "x.h5ad")
    with pytest.raises(BioRunError) as ei:
        r.resolve_data_path(escape)
    assert ei.value.error_code == "SC_PATH_FORBIDDEN"


# === dataset_id 幂等键（spec §3.3） ===

def test_compute_dataset_id_stable_and_size_sensitive(tmp_path):
    """同路径同大小 → 同 id；大小变化 → 新 id。"""
    f = tmp_path / "a.h5ad"
    f.write_bytes(b"12345")
    id1 = compute_dataset_id(str(f))
    assert id1 == compute_dataset_id(str(f))
    f.write_bytes(b"123456")  # 大小变化
    assert compute_dataset_id(str(f)) != id1


# === run()：容器执行错误链 ===

def _fake_proc(monkeypatch, *, rc=0, stdout="{}", stderr=""):
    proc = subprocess.CompletedProcess(
        args=[], returncode=rc, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(
        "orchestrator.tools.bio.bio_runner.subprocess.run",
        lambda *a, **k: proc)
    return proc


def test_run_success_parses_json(monkeypatch, roots, tmp_path):
    out = {"ok": True, "dataset_ref": "abc", "n_cells": 10}
    _fake_proc(monkeypatch, stdout=json.dumps(out))
    r = _runner(roots, tmp_path)
    assert r.run("load", {"path": "a.h5ad"}) == out


def test_run_nonzero_exit(monkeypatch, roots, tmp_path):
    _fake_proc(monkeypatch, rc=1, stderr="boom")
    r = _runner(roots, tmp_path)
    with pytest.raises(BioRunError) as ei:
        r.run("load", {})
    assert ei.value.error_code == "SC_SCRIPT_FAILED"


def test_run_timeout(monkeypatch, roots, tmp_path):
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=5)
    monkeypatch.setattr(
        "orchestrator.tools.bio.bio_runner.subprocess.run", _raise)
    r = _runner(roots, tmp_path)
    with pytest.raises(BioRunError) as ei:
        r.run("load", {})
    assert ei.value.error_code == "SC_TIMEOUT"


def test_run_non_json_output(monkeypatch, roots, tmp_path):
    _fake_proc(monkeypatch, stdout="not json")
    r = _runner(roots, tmp_path)
    with pytest.raises(BioRunError) as ei:
        r.run("load", {})
    assert ei.value.error_code == "SC_OUTPUT_INVALID"


def test_run_script_fail_error_code_passthrough(monkeypatch, roots, tmp_path):
    """脚本 fail() 输出（ok:false）：error_code 原样透传。"""
    _fake_proc(monkeypatch, stdout=json.dumps({
        "ok": False, "error_code": "SC_GENE_NOT_FOUND",
        "error_message": "genes not in dataset: CD3D"}))
    r = _runner(roots, tmp_path)
    with pytest.raises(BioRunError) as ei:
        r.run("plot", {"genes": ["CD3D"]})
    assert ei.value.error_code == "SC_GENE_NOT_FOUND"


def test_run_docker_cmd_has_limits_and_network_none(
        monkeypatch, roots, tmp_path):
    """docker 命令必须含 --network none / --cpus / --memory 限额。"""
    captured: dict = {}

    def _capture(cmd, **k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(
        "orchestrator.tools.bio.bio_runner.subprocess.run", _capture)
    r = _runner(roots, tmp_path)
    r.run("qc", {"dataset_id": "x"})
    cmd = captured["cmd"]
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert "--network" in cmd and "none" in cmd
    assert "--cpus" in cmd and "--memory" in cmd
    assert cmd[-1] == "/opt/sc_tools/qc.py"
