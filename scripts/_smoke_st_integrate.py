"""st_integrate 冒烟（Phase 69 多切片整合，spec
2026-09-19-st-integrate-design.md §10）：合成双切片 + 容器断网跑。

场景：
①harmony（默认引擎显式传）：双片各 400 spot（20×20 网格）× 600 共享
  基因，片内左右两域 marker + 片 B 叠批次效应 → ok + dataset_ref=
  stinta__stintb__harmony + representation=X_pca_harmony + n_genes=600
  （n_top_hvg=min(2000,600)=600 全选）+ 三产物落盘；
②spatial_offset=true：offset 后 note 带勿喂空间 kNN 提示；
③bbknn 引擎：neighbors_within_batch=8（max(3, round(15/2))）；
④dataset_refs 单片 → INVALID_INPUT；
⑤dataset_refs 重复 → INVALID_INPUT；
⑥method 非法 → INVALID_INPUT；
⑦ref 不存在 → ST_INTEG_REF_MISSING；
⑧两片基因零交集 → ST_INTEG_NO_OVERLAP；
⑨slice_col 已存在于 obs → INVALID_INPUT。

数据写 filtered.h5ad（load_adata filtered→raw 回退链首选）。
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

N_SIDE, N_GENES = 20, 600


def build(ds: str, batch_boost: bool = False, slice_col: str | None = None,
          fake_genes: bool = False) -> None:
    """合成单切片：400 spot 两域（x<10=D1 / x>=10=D2 各 200），
    D1/D2 各 40 marker 高表达；batch_boost=片级批次效应（基础率翻倍
    + 30 技术基因）；slice_col=预置同名 obs 列（冲突场景）；
    fake_genes=600 假基因（零交集场景）。"""
    n = N_SIDE * N_SIDE
    xs = np.repeat(np.arange(N_SIDE), N_SIDE).astype(float)
    ys = np.tile(np.arange(N_SIDE), N_SIDE).astype(float)
    if fake_genes:
        genes = [f"FAKE{i:03d}" for i in range(N_GENES)]
    else:
        genes = [f"G{i:03d}" for i in range(N_GENES)]
    base_rate = 2.0 if batch_boost else 1.0
    X = rng.poisson(base_rate, (n, N_GENES)).astype(np.float32)
    d1 = xs < 10
    X[d1, 0:40] = rng.poisson(40, (int(d1.sum()), 40)).astype(np.float32)  # D1 marker
    X[~d1, 40:80] = rng.poisson(40, (int((~d1).sum()), 40)).astype(np.float32)  # D2 marker
    if batch_boost:
        X[:, 500:530] = rng.poisson(30, (n, 30)).astype(np.float32)  # 技术基因
    import anndata as ad

    a = ad.AnnData(X=X)
    a.var_names = genes
    a.obs_names = [f"s{i}" for i in range(n)]
    a.obs["domain"] = pd.Categorical(np.where(d1, "D1", "D2"))
    if slice_col is not None:
        a.obs[slice_col] = "preset"
    a.obsm["spatial"] = np.c_[xs, ys] * 100.0
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "filtered.h5ad")


build("stinta")
build("stintb", batch_boost=True)
build("stintc", slice_col="slice")
build("stintx", fake_genes=True)


def run_st(**kw):
    """容器内跑 st_integrate.py（stdin JSON），严格口径整体解析 stdout。"""
    payload = json.dumps(kw)
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/st_integrate.py"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=900,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① harmony 整合双切片
o1 = run_st(dataset_refs=["stinta", "stintb"], method="harmony")
assert o1["ok"], o1
assert o1["dataset_ref"] == "stinta__stintb__harmony", o1["dataset_ref"]
assert o1["parent_refs"] == ["stinta", "stintb"], o1
assert o1["method"] == "harmony" and o1["n_slices"] == 2, o1
assert o1["slice_sizes"] == {"stinta": 400, "stintb": 400}, o1["slice_sizes"]
assert o1["n_cells"] == 800, o1
assert o1["n_genes"] == 600, o1["n_genes"]
assert o1["representation"] == "X_pca_harmony", o1
assert o1["n_clusters"] >= 2, o1
assert "spatial_png" in o1 and o1["spatial_offset"] is False, o1
for f in ("processed.h5ad", "umap_integrated.png", "spatial_slices.png"):
    assert (WS / "stinta__stintb__harmony" / f).exists(), f

# ② spatial_offset=true（展示用并排 + note 钉注）
o2 = run_st(dataset_refs=["stinta", "stintb"], spatial_offset=True)
assert o2["ok"] and o2["spatial_offset"] is True, o2
assert "display-only offsets" in o2["note"], o2["note"]
assert (WS / o2["dataset_ref"] / "spatial_slices.png").exists(), o2

# ③ bbknn 引擎
o3 = run_st(dataset_refs=["stinta", "stintb"], method="bbknn")
assert o3["ok"], o3
assert o3["dataset_ref"] == "stinta__stintb__bbknn", o3["dataset_ref"]
assert o3["method"] == "bbknn" and o3["neighbors_within_batch"] == 8, o3

# ④ 单片 → INVALID_INPUT
o4 = run_st(dataset_refs=["stinta"])
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4

# ⑤ 重复 ref → INVALID_INPUT
o5 = run_st(dataset_refs=["stinta", "stinta"])
assert not o5["ok"] and o5["error_code"] == "INVALID_INPUT", o5

# ⑥ method 非法 → INVALID_INPUT
o6 = run_st(dataset_refs=["stinta", "stintb"], method="magic")
assert not o6["ok"] and o6["error_code"] == "INVALID_INPUT", o6

# ⑦ ref 不存在 → ST_INTEG_REF_MISSING
o7 = run_st(dataset_refs=["stinta", "stint_ghost"])
assert not o7["ok"] and o7["error_code"] == "ST_INTEG_REF_MISSING", o7

# ⑧ 基因面板零交集 → ST_INTEG_NO_OVERLAP
o8 = run_st(dataset_refs=["stinta", "stintx"])
assert not o8["ok"] and o8["error_code"] == "ST_INTEG_NO_OVERLAP", o8

# ⑨ slice_col 已存在于 obs → INVALID_INPUT
o9 = run_st(dataset_refs=["stinta", "stintc"], slice_col="slice")
assert not o9["ok"] and o9["error_code"] == "INVALID_INPUT", o9

print(
    f"SMOKE OK | harmony 2-slice ref={o1['dataset_ref']}"
    f" cells={o1['n_cells']} genes={o1['n_genes']} clusters={o1['n_clusters']}"
    f" | offset note ok | bbknn nwb={o3['neighbors_within_batch']}"
    f" | single/dupe refs rejected | bad method rejected"
    f" | ghost ref rejected | disjoint genes rejected | slice col clash rejected"
)
