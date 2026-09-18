"""sc_integrate harmony 冒烟（Phase 69 双引擎，spec
2026-09-19-st-integrate-design.md §10）：合成双批 + 容器断网跑。

场景：
①harmony：600 细胞（S1/S2 两批各 300）× 500 基因，S2 叠 40 技术基因
  批次效应 → ok + dataset_ref=scint_harm_harmony + representation=
  X_pca_harmony + 产物（processed.h5ad/umap_integrated.png）落盘；
②bbknn 对照：同数据 bbknn 引擎 → neighbors_within_batch=8
  （max(3, round(15/2))）+ dataset_ref=scint_harm_bbknn；
③batch 列不存在 → SCRIPT_ERROR（ValueError 兜底口径）；
④method 非法 → SCRIPT_ERROR；
⑤batch 仅单值 → SCRIPT_ERROR。

数据写 filtered.h5ad：sc_integrate 对已过滤数据集跳过 min_genes=600
默认过滤（泊松低表达合成数据会被整批滤空）。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", Path(__file__).resolve().parents[1] / "bio_workspace"))
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")
BASE = ["docker", "run", "--rm", "-i", "--network", "none", "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]

rng = np.random.default_rng(7)


def build() -> None:
    """合成双批库：600×500，批内 A/B 两群共享 marker（40/40），S2 叠
    40 技术基因高表达（批次效应）——harmony 可校、bbknn 可比。"""
    n, n_genes = 600, 500
    genes = [f"G{i:03d}" for i in range(n_genes)]
    X = rng.poisson(1.0, (n, n_genes)).astype(np.float32)
    grp = ["A"] * 150 + ["B"] * 150 + ["A"] * 150 + ["B"] * 150
    for j, g in enumerate(grp):
        if g == "A":
            X[j, 0:40] = rng.poisson(40, 40).astype(np.float32)
        else:
            X[j, 40:80] = rng.poisson(40, 40).astype(np.float32)
    X[300:, 460:500] = rng.poisson(30, (300, 40)).astype(np.float32)  # S2 批次效应
    import anndata as ad

    a = ad.AnnData(X=X)
    a.var_names = genes
    a.obs_names = [f"c{i}" for i in range(n)]
    a.obs["sample"] = pd.Categorical(["S1"] * 300 + ["S2"] * 300)
    a.obs["group"] = pd.Categorical(grp)
    a.obs["const"] = "X"  # 单值列：⑤ 拒收场景（n_batch<2 ValueError）
    d = WS / "scint_harm"
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "filtered.h5ad")


build()


def run_sc(**kw):
    """容器内跑 integrate.py（stdin JSON），严格口径整体解析 stdout。"""
    payload = json.dumps({"dataset_id": "scint_harm", **kw})
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/integrate.py"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=900,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① harmony 引擎
o1 = run_sc(batch="sample", method="harmony")
assert o1["ok"], o1
assert o1["dataset_ref"] == "scint_harm_harmony", o1["dataset_ref"]
assert o1["method"] == "harmony" and o1["n_batches"] == 2, o1
assert o1["representation"] == "X_pca_harmony", o1
assert o1["n_cells"] == 600 and o1["n_clusters"] >= 2, o1
assert (WS / "scint_harm_harmony" / "processed.h5ad").exists(), "processed.h5ad missing"
assert (WS / "scint_harm_harmony" / "umap_integrated.png").exists(), "umap missing"

# ② bbknn 引擎对照（同数据）
o2 = run_sc(batch="sample", method="bbknn")
assert o2["ok"], o2
assert o2["dataset_ref"] == "scint_harm_bbknn", o2["dataset_ref"]
assert o2["method"] == "bbknn" and o2["neighbors_within_batch"] == 8, o2

# ③ batch 列不存在 → SCRIPT_ERROR
o3 = run_sc(batch="nope", method="harmony")
assert not o3["ok"] and o3["error_code"] == "SCRIPT_ERROR", o3

# ④ method 非法 → SCRIPT_ERROR
o4 = run_sc(batch="sample", method="magic")
assert not o4["ok"] and o4["error_code"] == "SCRIPT_ERROR", o4

# ⑤ batch 仅单值列（const）→ SCRIPT_ERROR（integrate.py n_batch<2 兜底）
o5 = run_sc(batch="const", method="harmony")
assert not o5["ok"] and o5["error_code"] == "SCRIPT_ERROR", o5

print(
    f"SMOKE OK | harmony ref={o1['dataset_ref']} reps={o1['representation']}"
    f" clusters={o1['n_clusters']} | bbknn nwb={o2['neighbors_within_batch']}"
    f" | bad batch col rejected | bad method rejected | single batch rejected"
)
