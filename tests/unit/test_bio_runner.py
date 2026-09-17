"""Phase 20：BioRunner 单测（路径白名单 / dataset_id / 容器执行错误链）。

docker subprocess 全部 mock，不起真实容器。
"""
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.tools.bio.bio_runner import (
    BioRunError,
    BioRunner,
    compute_dataset_id,
    touch_last_access,
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


def test_compute_dataset_id_dir_aggregate(tmp_path):
    """目录聚合 hash：任一文件变化换 id；目录不动 id 稳定。"""
    from orchestrator.tools.bio.bio_runner import compute_dataset_id_dir

    d = tmp_path / "spaceranger_out"
    d.mkdir()
    (d / "filtered_feature_bc_matrix.h5").write_bytes(b"v1")
    (d / "tissue_positions.csv").write_bytes(b"pos")

    id1 = compute_dataset_id_dir(str(d))
    id2 = compute_dataset_id_dir(str(d))
    assert id1 == id2 and len(id1) == 12

    (d / "tissue_positions.csv").write_bytes(b"pos-changed")
    id3 = compute_dataset_id_dir(str(d))
    assert id3 != id1


def test_compute_dataset_id_dir_missing_raises(tmp_path):
    import pytest

    from orchestrator.tools.bio.bio_runner import BioRunError, compute_dataset_id_dir
    with pytest.raises(BioRunError, match="not found"):
        compute_dataset_id_dir(str(tmp_path / "nope"))


def test_compute_dataset_id_missing_file_raises(tmp_path):
    """单文件版对齐目录版：不存在路径抛 BioRunError 而非原生 OSError。"""
    import pytest

    from orchestrator.tools.bio.bio_runner import BioRunError
    with pytest.raises(BioRunError, match="not found"):
        compute_dataset_id(str(tmp_path / "nope.h5ad"))


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


def test_run_script_fail_json_passthrough_on_rc1(monkeypatch, roots, tmp_path):
    """脚本 fail() JSON + exit 1：透传 error_code（Phase 21 真机发现）。"""
    _fake_proc(
        monkeypatch, rc=1,
        stdout=json.dumps({"ok": False,
                           "error_code": "ST_GENES_NOT_FOUND",
                           "error_message": "none of genes found"}),
        stderr="some warning lines")
    r = _runner(roots, tmp_path)
    with pytest.raises(BioRunError) as ei:
        r.run("plot", {})
    assert ei.value.error_code == "ST_GENES_NOT_FOUND"
    assert "none of genes" in str(ei.value)


def test_run_timeout(monkeypatch, roots, tmp_path):
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=5)
    monkeypatch.setattr(
        "orchestrator.tools.bio.bio_runner.subprocess.run", _raise)
    r = _runner(roots, tmp_path)
    with pytest.raises(BioRunError) as ei:
        r.run("load", {})
    assert ei.value.error_code == "SC_TIMEOUT"


def test_run_timeout_kills_named_container(monkeypatch, roots, tmp_path):
    """超时兜底 docker kill 同一命名容器（僵尸容器修复，2026-09-12
    真实 Visium 验收发现：subprocess timeout 只杀 CLI 客户端）。"""
    calls: list[list[str]] = []

    def _raise(cmd, **k):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd="docker", timeout=5)

    monkeypatch.setattr(
        "orchestrator.tools.bio.bio_runner.subprocess.run", _raise)
    r = _runner(roots, tmp_path)
    with pytest.raises(BioRunError) as ei:
        r.run("load", {})
    assert ei.value.error_code == "SC_TIMEOUT"
    run_cmd, kill_cmd = calls[0], calls[1]
    cname = run_cmd[run_cmd.index("--name") + 1]
    assert cname.startswith("bio-")
    assert kill_cmd == ["docker", "kill", cname]


def test_run_timeout_kill_failure_still_raises(monkeypatch, roots, tmp_path):
    """docker kill 自身失败（容器已退/守护进程异常）不掩盖 SC_TIMEOUT。"""
    def _mixed(cmd, **k):
        if cmd[1] == "kill":
            raise OSError("daemon unreachable")
        raise subprocess.TimeoutExpired(cmd="docker", timeout=5)

    monkeypatch.setattr(
        "orchestrator.tools.bio.bio_runner.subprocess.run", _mixed)
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


# === run() 参数化：镜像 / 脚本目录覆盖（Phase 21 st_* 工具链） ===

@pytest.fixture
def runner(roots, tmp_path):
    """默认镜像 bio:test 的 BioRunner 实例。"""
    return _runner(roots, tmp_path)


@pytest.fixture
def fake_docker_ok(monkeypatch):
    """mock subprocess.run 成功，记录完整 docker cmd 供断言。"""
    rec = SimpleNamespace(cmd=[])

    def _capture(cmd, **k):
        rec.cmd = cmd
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(
        "orchestrator.tools.bio.bio_runner.subprocess.run", _capture)
    return rec


def test_run_uses_custom_image_and_script_dir(runner, fake_docker_ok):
    """run(image=..., script_dir=...) 覆盖实例默认值（st 镜像走 /opt/st_tools）。"""
    out = runner.run(
        "load", {"path": "x"},
        image="feishu-research-agent/bio:st-cpu-latest",
        script_dir="/opt/st_tools")
    assert out["ok"] is True
    cmd = fake_docker_ok.cmd
    img_idx = cmd.index("feishu-research-agent/bio:st-cpu-latest")
    script_idx = cmd.index("python")
    assert cmd[script_idx + 1] == "/opt/st_tools/load.py"
    assert img_idx < script_idx


def test_run_default_image_and_script_dir(runner, fake_docker_ok):
    """不传覆盖参数时沿用实例默认 image 与 /opt/sc_tools。"""
    runner.run("load", {})
    cmd = fake_docker_ok.cmd
    assert "bio:test" in cmd  # runner fixture 默认镜像（同 _runner）
    assert "/opt/sc_tools/load.py" in cmd


# === Phase 23：GC .last_access 打点 ===

def test_touch_last_access_creates_marker(tmp_path):
    """目录存在时创建 .last_access 打点文件。"""
    d = tmp_path / "abcdef123456"
    d.mkdir()
    touch_last_access(str(tmp_path), "abcdef123456")
    assert (d / ".last_access").exists()


def test_touch_last_access_updates_mtime(tmp_path):
    """已有打点文件时刷新 mtime（LRU 的"最近使用"依据）。"""
    d = tmp_path / "abcdef123456"
    d.mkdir()
    marker = d / ".last_access"
    marker.touch()
    old = time.time() - 3600
    os.utime(marker, (old, old))
    touch_last_access(str(tmp_path), "abcdef123456")
    assert marker.stat().st_mtime > old


def test_touch_last_access_skips_invalid(tmp_path):
    """None / 非 12hex（路径穿越防护）/ 目录不存在 → 静默跳过。"""
    touch_last_access(str(tmp_path), None)
    touch_last_access(str(tmp_path), "../escape")
    touch_last_access(str(tmp_path), "0123456789ab")  # 合法 hex 但目录不存在
    assert list(tmp_path.iterdir()) == []


def test_touch_last_access_failure_non_blocking(tmp_path, monkeypatch):
    """打点 IO 异常不抛（best-effort，宁可漏打点不可挂任务）。"""
    d = tmp_path / "abcdef123456"
    d.mkdir()

    def boom(*a, **k):
        raise OSError("read-only fs")

    monkeypatch.setattr(Path, "touch", boom)
    touch_last_access(str(tmp_path), "abcdef123456")  # 不抛即通过


def test_run_touches_last_access(tmp_path, monkeypatch):
    """run() 开头对 args.dataset_id 对应目录打点（docker 调用 mock 掉）。"""
    import subprocess as sp

    ws = tmp_path / "ws"
    d = ws / "abcdef123456"
    d.mkdir(parents=True)
    runner = BioRunner(image="img", workspace_root=str(ws), data_roots=[])

    class _P:
        returncode = 0
        stdout = '{"ok": true}'
        stderr = ""

    monkeypatch.setattr(sp, "run", lambda *a, **k: _P())
    runner.run("qc", {"dataset_id": "abcdef123456"})
    assert (d / ".last_access").exists()


# === Phase 25: --gpus 透传 ===

def test_run_gpus_adds_flag(runner, fake_docker_ok):
    """gpus=True → docker cmd 含 --gpus all（--name 占位后按值定位）。"""
    runner.run("process", {"dataset_id": "abcdef123456"}, gpus=True)
    cmd = fake_docker_ok.cmd
    gpus_idx = cmd.index("--gpus")
    assert cmd[gpus_idx + 1] == "all"


def test_run_default_no_gpus(runner, fake_docker_ok):
    """默认 gpus=False → cmd 不含 --gpus。"""
    runner.run("qc", {"dataset_id": "abcdef123456"})
    assert "--gpus" not in fake_docker_ok.cmd


# === 执行面统一挂账③：工具级 cpus/memory 覆写（2026-09-17） ===

def test_run_resource_override_applied(runner, fake_docker_ok):
    """run(cpus=..., memory=...) 覆写实例默认，落到 docker cmd。"""
    runner.run("scenic", {"dataset_id": "abcdef123456"},
               cpus="8", memory="32g")
    cmd = fake_docker_ok.cmd
    assert cmd[cmd.index("--cpus") + 1] == "8"
    assert cmd[cmd.index("--memory") + 1] == "32g"


def test_run_resource_default_when_not_overridden(runner, fake_docker_ok):
    """不传覆写 → 沿用实例默认 cpus=4 / memory=16g。"""
    runner.run("qc", {"dataset_id": "abcdef123456"})
    cmd = fake_docker_ok.cmd
    assert cmd[cmd.index("--cpus") + 1] == "4"
    assert cmd[cmd.index("--memory") + 1] == "16g"
