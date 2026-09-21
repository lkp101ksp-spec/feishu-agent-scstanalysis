"""st_score_weight 冒烟（Phase 76，spec 2026-09-21 §5.3）：
合成 deconv.h5ad（q05 丰度，CellA 集中左半）+ 合成 scores csv（左半
TGFb 高分注入）→ 容器断网跑 → 真值回收 + 全零型剔除 + 三拒收。

场景：
①主场景：CellA×TGFb 为 W 全局最高且 > CellB×TGFb、CellC（全零）入
  dropped_celltypes、n_spots_overlap=40、两产物落盘、
  top_by_celltype["CellA"][0]=="TGFb"；
②缺 deconv.h5ad → ST_WEIGHT_NO_DECONV + 引导 st_deconvolve；
③source=st_metabolism 无产物 → INVALID_INPUT + 引导 st_metabolism；
④spot 索引全错位（X 前缀）→ INVALID_INPUT + 重合率报数。

合成数据：40 spot（8×5 网格左半/右半），3 细胞型（CellA 左半 3.0/
右半 0.05、CellB 均匀 1.0、CellC 全零），14 通路（TGFb 左半 5.0/
右半 0.1，其余 N(0,0.05)，seed=7）。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", Path(__file__).resolve().parents[1] / "bio_workspace"))
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")

REPO = Path(__file__).resolve().parents[1]
MOUNTS = [
    "-v",
    f"{str(WS).replace(chr(92), '/')}:/ws",
    # sc_tools 整目录挂载对齐 handler 的 script_dir 行为（Phase 65 先例）
    "-v",
    f"{str(REPO / 'sandbox' / 'sc_tools').replace(chr(92), '/')}:/opt/sc_tools",
]
BASE = ["docker", "run", "--rm", "-i", "--network", "none", *MOUNTS, IMG]

PW = ["TGFb", "EGFR", "JAK-STAT"] + [f"PW{i:02d}" for i in range(11)]


def build_ds(ds: str, misalign: bool = False, with_deconv: bool = True) -> str:
    """合成 {ds}/st_genescore/st_progeny_scores.csv + deconv.h5ad；
    misalign 时 scores 索引用 X 前缀（与 deconv 零重合）。"""
    import anndata as ad

    rng = np.random.default_rng(7)
    n = 40
    names = [f"S{i:02d}" for i in range(n)]
    left = (np.arange(n) % 8) < 4
    s = rng.normal(0.0, 0.05, (n, len(PW)))
    s[:, 0] = np.where(left, 5.0, 0.1)  # TGFb 左半高分注入
    sc_df = pd.DataFrame(s, columns=PW)
    sc_df.insert(0, "spatial_domain", np.where(left, "D1", "D2"))
    sc_df.index = [f"X{i:02d}" for i in range(n)] if misalign else names
    d = WS / ds
    (d / "st_genescore").mkdir(parents=True, exist_ok=True)
    sc_df.to_csv(d / "st_genescore" / "st_progeny_scores.csv")
    if with_deconv:
        abund = pd.DataFrame(
            {
                "CellA": np.where(left, 3.0, 0.05),
                "CellB": np.full(n, 1.0),
                "CellC": np.zeros(n),  # 全零 → dropped_celltypes
            },
            index=names,
        )
        a = ad.AnnData(X=np.ones((n, 3), dtype=np.float32))
        a.obs_names = names
        a.var_names = ["G1", "G2", "G3"]
        a.obsm["q05_cell_abundance_w_sf"] = abund
        a.write_h5ad(d / "deconv.h5ad")
    return ds


DS_MAIN = build_ds("_smoke_st_weight_main")
DS_NODECONV = build_ds("_smoke_st_weight_nodeconv", with_deconv=False)
DS_MISALIGN = build_ds("_smoke_st_weight_misalign", misalign=True)


def run_sw(**kw):
    """容器内跑 st_score_weight.py（stdin JSON），整体解析 stdout。"""
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/st_score_weight.py"],
        input=json.dumps(kw),
        capture_output=True,
        text=True,
        timeout=600,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 主场景：真值回收 + 全零型剔除 + 产物落盘
o1 = run_sw(dataset_id=DS_MAIN)
assert o1["ok"], o1
assert o1["source"] == "st_genescore" and o1["n_spots_overlap"] == 40, o1
assert o1["n_celltypes"] == 2 and o1["n_pathways"] == 14, o1
assert o1["dropped_celltypes"] == ["CellC"], o1
for key in ("scores_csv", "heatmap_png"):
    assert (WS / Path(o1["products"][key]).relative_to("/ws")).exists(), o1["products"][key]
assert o1["top_by_celltype"]["CellA"][0] == "TGFb", o1["top_by_celltype"]
w = pd.read_csv(WS / Path(o1["products"]["scores_csv"]).relative_to("/ws"), index_col=0)
assert float(w.loc["CellA", "TGFb"]) == float(w.to_numpy().max()), w
assert float(w.loc["CellA", "TGFb"]) > float(w.loc["CellB", "TGFb"]), w
print(f"① 主场景：CellA×TGFb={w.loc['CellA', 'TGFb']:.3f} 全局最高 "
      f"+ CellC 剔除 + 两产物落盘 OK")

# ② 缺 deconv.h5ad 拒收
o2 = run_sw(dataset_id=DS_NODECONV)
assert not o2["ok"] and o2["error_code"] == "ST_WEIGHT_NO_DECONV", o2
assert "st_deconvolve" in o2["error_message"], o2
print("② 缺 deconv ST_WEIGHT_NO_DECONV + 引导 OK")

# ③ 缺 source 产物拒收（st_metabolism 未跑）
o3 = run_sw(dataset_id=DS_MAIN, source="st_metabolism")
assert not o3["ok"] and o3["error_code"] == "INVALID_INPUT", o3
assert "st_metabolism" in o3["error_message"], o3
print("③ 缺 scores INVALID_INPUT + 引导 st_metabolism OK")

# ④ spot 索引错位拒收（0% 重合）
o4 = run_sw(dataset_id=DS_MISALIGN)
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4
assert "重合率" in o4["error_message"], o4
print("④ 索引错位 INVALID_INPUT + 重合率报数 OK")

print("\nSMOKE OK: st_score_weight 4 场景全绿")
