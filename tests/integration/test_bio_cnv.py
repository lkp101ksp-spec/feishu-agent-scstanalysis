"""B1 sc_cnv 真容器集成测试（需 Docker + bio:cpu-latest，缺则自动 skip）。

镜像构建：
    docker build -t feishu-research-agent/bio:cpu-latest sandbox -f sandbox/bio.Dockerfile
合成数据在容器内构造（宿主 venv 无 scanpy）：免疫参考群（T/B cells）+
人工 CNV 信号"恶性"群（chr7 基因×1.8 gain、chr10 ×0.6 loss）→
sc_cnv 双后端 → 断言恶性判定集中在造的恶性群、obs 写回与产物齐全。
"""
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_IMAGE = "feishu-research-agent/bio:cpu-latest"


def _docker_ready() -> bool:
    """docker 可用且 bio 镜像已构建。"""
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10)
    except Exception:
        return False
    inspect = subprocess.run(
        ["docker", "image", "inspect", _IMAGE],
        capture_output=True, timeout=10,
    )
    return inspect.returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_ready(), reason="docker 不可用或 bio 镜像未构建"
)

_BUILDER = r'''
import numpy as np
import pandas as pd
import anndata as ad

rng = np.random.default_rng(42)
pos = pd.read_csv("/opt/cnv/gene_pos_grch38.tsv", sep="\t")
sel = pd.concat([pos[pos["chromosome"] == f"chr{c}"].head(220)
                 for c in (1, 2, 3, 7, 10)]).drop_duplicates("gene_name")
genes = sel["gene_name"].tolist()
n_g = len(genes)
n_t, n_b, n_m = 120, 120, 150
base = rng.poisson(2.0, (n_t + n_b, n_g)).astype(np.float32)
mal = rng.poisson(2.0, (n_m, n_g)).astype(np.float32)
gain = sel["chromosome"].eq("chr7").to_numpy()
loss = sel["chromosome"].eq("chr10").to_numpy()
mal[:, gain] = rng.poisson(3.6, (n_m, int(gain.sum()))).astype(np.float32)
mal[:, loss] = rng.poisson(0.72, (n_m, int(loss.sum()))).astype(np.float32)
counts = np.vstack([base, mal])
barcodes = [f"c{i}" for i in range(counts.shape[0])]
ct = ["T cells"] * n_t + ["B cells"] * n_b + ["Epithelial tumor"] * n_m
adata = ad.AnnData(
    X=counts,
    obs=pd.DataFrame({"celltype": ct}, index=barcodes),
    var=pd.DataFrame(index=pd.Index(genes)))
adata.var_names_make_unique()
adata.write_h5ad("/ws/itest/filtered.h5ad")
proc = ad.AnnData(
    X=np.zeros((counts.shape[0], 10), dtype=np.float32),
    obs=pd.DataFrame({"celltype": ct}, index=barcodes),
    var=pd.DataFrame(index=[f"g{i}" for i in range(10)]))
proc.obsm["X_umap"] = rng.normal(size=(counts.shape[0], 2))
proc.write_h5ad("/ws/itest/processed.h5ad")
print("built", counts.shape)
'''


@pytest.fixture(scope="module")
def bio_ws(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """容器内构造合成数据集 itest，返回宿主侧 workspace 路径。"""
    ws = tmp_path_factory.mktemp("biows")
    (ws / "itest").mkdir()
    bdir = tmp_path_factory.mktemp("builder")
    (bdir / "build_data.py").write_text(_BUILDER, encoding="utf-8")
    out = subprocess.run(
        ["docker", "run", "--rm",
         "-v", f"{ws}:/ws", "-v", f"{bdir}:/builder",
         _IMAGE, "python", "/builder/build_data.py"],
        capture_output=True, timeout=600)
    assert out.returncode == 0, out.stderr.decode()[-2000:]
    return ws


def _run_cnv(ws: Path, payload: dict[str, Any],
             timeout: int = 1800) -> dict[str, Any]:
    """stdin JSON 喂 cnv.py，解析 stdout 末行 JSON。"""
    raw = json.dumps(payload).encode()
    out = subprocess.run(
        ["docker", "run", "--rm", "-i", "-v", f"{ws}:/ws",
         _IMAGE, "python", "/opt/sc_tools/cnv.py"],
        input=raw, capture_output=True, timeout=timeout)
    assert out.returncode == 0, out.stderr.decode()[-2000:]
    res: dict[str, Any] = json.loads(out.stdout.decode().splitlines()[-1])
    return res


@pytest.mark.parametrize("method", ["infercnvpy", "cnvturbo"])
def test_sc_cnv_detects_synthetic_malignant(bio_ws: Path,
                                            method: str) -> None:
    """合成 CNV 信号（chr7 gain/chr10 loss）双后端判定集中于恶性群。"""
    res = _run_cnv(bio_ws, {"dataset_id": "itest", "method": method,
                            "celltype_col": "celltype"})
    assert res["ok"] is True
    assert set(res["matched_references"]) >= {"T cells", "B cells"}
    assert res["genes"]["positioned"] >= 1000
    epi_mal = res["malignant_by_celltype"].get("Epithelial tumor", 0)
    assert epi_mal > 0
    assert epi_mal / res["n_malignant"] >= 0.8  # 精度：≥80% 落在造的恶性群
    for key in ("chromosome_heatmap_png", "score_umap_png",
                "subclone_umap_png", "celltype_summary_csv",
                "subclone_by_chromosome_csv"):
        host = Path(res[key].replace("/ws", str(bio_ws)))
        assert host.exists(), f"missing {key}: {host}"
    # obs 写回（容器内回读验证）
    check = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{bio_ws}:/ws", _IMAGE,
         "python", "-c",
         "import anndata as ad; a = ad.read_h5ad('/ws/itest/processed.h5ad')"
         "; assert 'cnv_score' in a.obs and 'is_malignant' in a.obs and "
         "'cnv_subclone' in a.obs; print('obs-ok')"],
        capture_output=True, timeout=300)
    assert b"obs-ok" in check.stdout, check.stderr.decode()[-2000:]


def test_sc_cnv_ref_groups_miss(bio_ws: Path) -> None:
    """ref_groups 值不存在 → 明确报错列可用值（planner 自纠）。"""
    res = _run_cnv(bio_ws, {"dataset_id": "itest",
                            "celltype_col": "celltype",
                            "ref_groups": ["NotExists"]}, timeout=600)
    assert res["ok"] is False
    assert "ref_groups not in column values" in res["error_message"]
    assert "Epithelial tumor" in res["error_message"]  # 错误里列可用值
