"""sc_load .rds 支持（2026-09-29 PDAC 事故修复②）Python 半桥单测。

R 桥（rds2mtx.R）真机冒烟见测试总结；此处用 fake Rscript 钉住
_read_rds 的组装契约：writeMM genes×cells → 转置 cells×genes、
obs.csv 挂到 .obs、R 侧非零退出 → fail + SystemExit。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import scipy.io as sio
import scipy.sparse as sp

SC_TOOLS = Path(__file__).resolve().parents[2] / "sandbox" / "sc_tools"
sys.path.insert(0, str(SC_TOOLS))

import load as sc_load  # noqa: E402


class _Proc:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


def _fake_rscript_ok(cmd: list[str], **_: object) -> _Proc:
    """模拟 rds2mtx.R：genes×cells mtx + barcodes/features/obs.csv。"""
    out = Path(cmd[3])
    genes = ["G1", "G2", "G3"]
    cells = ["c1", "c2"]
    x_gc = sp.csr_matrix(np.array([[1, 0], [0, 2], [3, 4]]))
    sio.mmwrite(out / "matrix.mtx", x_gc)
    (out / "barcodes.tsv").write_text("\n".join(cells))
    (out / "features.tsv").write_text("\n".join(genes))
    pd.DataFrame({"cell": cells, "ctype": ["T", "B"]}).to_csv(
        out / "obs.csv", index=False)
    return _Proc(0)


def test_read_rds_assembles_anndata(monkeypatch, tmp_path):
    monkeypatch.setattr(sc_load.subprocess, "run", _fake_rscript_ok)
    adata = sc_load._read_rds(tmp_path / "fake.rds")
    # genes×cells → cells×genes 转置生效
    assert adata.shape == (2, 3)
    assert list(adata.obs_names) == ["c1", "c2"]
    assert list(adata.var_names) == ["G1", "G2", "G3"]
    assert list(adata.obs["ctype"]) == ["T", "B"]
    assert adata.X[1, 1] == 2 and adata.X[0, 2] == 3


def test_read_rds_fail_fast_on_r_error(monkeypatch, tmp_path, capsys):
    def _fail(cmd: list[str], **_: object) -> _Proc:
        return _Proc(1, "Error: unsupported rds object class: list")

    monkeypatch.setattr(sc_load.subprocess, "run", _fail)
    with pytest.raises(SystemExit):
        sc_load._read_rds(tmp_path / "fake.rds")
    out = capsys.readouterr().out
    assert "SC_RDS_CONVERT_FAILED" in out
    assert "unsupported rds object class" in out
