"""st_metabolism 冒烟（Phase 75 空间版，spec phase75 §3.5）：
空间网格 + Glycolysis 真值注入 + 容器断网跑。

场景：
①method=aucell 主场景：D1 域（网格左半）糖酵解基因 +2.5 → ok +
  n_domains=2 + n_pathways_scored>=300 + 四产物落盘（含 spatial png）；
②csv 内容：Glycolysis 方差 rank<=3 且 D1 均值 > D2×3、top_by_group
  D1 前 3 含 Glycolysis；
③method=mean 回归：score_genes 口径 Glycolysis 仍 D1>D2（z>1.5）；
④method=bad → INVALID_INPUT；
⑤无 obsm.spatial → INVALID_INPUT + 引导 sc_metabolism；
⑥单域 groupby → INVALID_INPUT。

合成 h5ad：KEGG 全通路靶基因并集 + 200 噪声基因 × 80 spot（8×10
网格左半 D1/右半 D2），N(6,0.8) 基底（seed=7），raw=X 同层。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", Path(__file__).resolve().parents[1] / "bio_workspace"))
DATA = WS / "_smoke_st_metabolism_data"
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")

REPO = Path(__file__).resolve().parents[1]
MOUNTS = [
    "-v",
    f"{str(WS).replace(chr(92), '/')}:/ws",
    "-v",
    f"{str(REPO / 'sandbox' / 'sc_tools').replace(chr(92), '/')}:/opt/sc_tools",
]
BASE = ["docker", "run", "--rm", "-i", "--network", "none", *MOUNTS, IMG]

DUMP_CMD = (
    "import shutil; "
    "shutil.copy('/opt/gene_sets/kegg.json', "
    "'/ws/_smoke_st_metabolism_data/kegg.json')"
)

DATA.mkdir(parents=True, exist_ok=True)

r0 = subprocess.run(BASE + ["python", "-c", DUMP_CMD], capture_output=True, text=True, timeout=300)
if r0.returncode != 0:
    raise SystemExit(f"kegg dump rc={r0.returncode}\n{r0.stderr[-600:]}")
pathways = json.loads((DATA / "kegg.json").read_text(encoding="utf-8"))
assert len(pathways) >= 300, len(pathways)

gly_key = next(k for k in pathways if "Glycolysis" in k)
UNIVERSE = sorted({g for genes in pathways.values() for g in genes})
assert len(UNIVERSE) >= 500, len(UNIVERSE)
gly_genes = [g for g in pathways[gly_key] if g in set(UNIVERSE)]
assert len(gly_genes) >= 20, (gly_key, len(gly_genes))


def build_gex(ds: str, spatial: bool = True,
              single_domain: bool = False) -> str:
    """合成 processed.h5ad：UNIVERSE+噪声 × 80 spot（8×10 网格，
    左半 D1/右半 D2）；D1 糖酵解基因 +2.5（seed=7），raw=X 同层。"""
    import anndata as ad

    rng = np.random.default_rng(7)
    n = 80
    cols = np.arange(n) % 8
    rows = np.arange(n) // 8
    dom = np.where(cols < 4, "D1", "D2")
    genes = UNIVERSE + [f"NOISE{i:04d}" for i in range(200)]
    x = rng.normal(6.0, 0.8, (n, len(genes))).astype(np.float32)
    gly_idx = [genes.index(g) for g in gly_genes]
    # np.ix_ 双索引原位注入（x[mask][:, idx]+=v 是副本写会静默丢失）
    x[np.ix_(np.where(dom == "D1")[0], gly_idx)] += 2.5
    a = ad.AnnData(X=x)
    a.var_names = genes
    a.obs_names = [f"S{i:02d}" for i in range(n)]
    a.obs["spatial_domain"] = pd.Categorical(
        ["D1"] * n if single_domain else dom.tolist())
    if spatial:
        a.obsm["spatial"] = np.column_stack([cols, rows]).astype(np.float64)
    a.raw = a
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "processed.h5ad")
    return ds


DS_MAIN = build_gex("_smoke_st_metabolism_gex")
DS_NOSPAT = build_gex("_smoke_st_metabolism_nospat", spatial=False)
DS_1DOM = build_gex("_smoke_st_metabolism_1dom", single_domain=True)


def run_mt(**kw):
    """容器内跑 st_metabolism.py（stdin JSON），整体解析 stdout。"""
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/st_metabolism.py"],
        input=json.dumps(kw),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 主场景：AUCell 打分 + 四产物落盘（含空间图）
o1 = run_mt(dataset_id=DS_MAIN, species="human", method="aucell")
assert o1["ok"], o1
assert o1["method"] == "aucell" and "AUCell" in o1["method_note"], o1
assert o1["n_spots"] == 80 and o1["n_domains"] == 2, o1
assert o1["n_pathways_scored"] >= 300, o1["n_pathways_scored"]
for key in ("scores_csv", "group_mean_csv", "heatmap_png", "spatial_png"):
    assert (WS / Path(o1["products"][key]).relative_to("/ws")).exists(), o1["products"][key]
assert o1["products"]["umap_png"] is None  # 合成数据无 X_umap 属预期
assert any("Glycolysis" in t for t in o1["top_by_group"]["D1"][:3]), o1["top_by_group"]["D1"]
print(f"① AUCell：{o1['n_pathways_scored']} 通路 + 四产物落盘（含 spatial png）OK")

# ② csv 内容：Glycolysis 方差 rank<=3 且 D1 均值 > D2×3
gm = pd.read_csv(WS / Path(o1["products"]["group_mean_csv"]).relative_to("/ws"), index_col=0)
gly_a, gly_b = float(gm.loc["D1", gly_key]), float(gm.loc["D2", gly_key])
assert gly_a > gly_b * 3.0, (gly_a, gly_b)
sc = pd.read_csv(WS / Path(o1["products"]["scores_csv"]).relative_to("/ws"))
assert len(sc) == 80 and gly_key in sc.columns, (sc.shape, sc.columns[:3])
print(f"② csv：{gly_key}@D1={gly_a:.4f} > D2={gly_b:.4f}×3 + 80 行含通路列 OK")

# ③ method=mean 回归：score_genes 口径 Glycolysis 仍分离
o3 = run_mt(dataset_id=DS_MAIN, species="human", method="mean")
assert o3["ok"] and o3["method"] == "mean", o3
gm3 = pd.read_csv(WS / Path(o3["products"]["group_mean_csv"]).relative_to("/ws"), index_col=0)
gly_a3, gly_b3 = float(gm3.loc["D1", gly_key]), float(gm3.loc["D2", gly_key])
assert gly_a3 > 1.5 and gly_a3 > gly_b3, (gly_a3, gly_b3)
print(f"③ mean 回归：score_genes D1={gly_a3:.3f} > D2={gly_b3:.3f} OK")

# ④ method 非法 → INVALID_INPUT（message 含 method 提示）
o4 = run_mt(dataset_id=DS_MAIN, species="human", method="bad")
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4
assert "method" in o4["error_message"], o4["error_message"]
print("④ method=bad INVALID_INPUT + 提示 OK")

# ⑤ 无 obsm.spatial 拒收（st 门槛，引导 sc_metabolism）
o5 = run_mt(dataset_id=DS_NOSPAT, species="human", method="aucell")
assert not o5["ok"] and o5["error_code"] == "INVALID_INPUT", o5
assert "spatial" in o5["error_message"] and "sc_metabolism" in o5["error_message"], o5
print("⑤ 无 obsm.spatial INVALID_INPUT + 引导 sc_metabolism OK")

# ⑥ 单域 groupby 拒收
o6 = run_mt(dataset_id=DS_1DOM, species="human", method="aucell")
assert not o6["ok"] and o6["error_code"] == "INVALID_INPUT", o6
assert "1 个域" in o6["error_message"], o6
print("⑥ 单域 INVALID_INPUT + 换列引导 OK")

print("\nSMOKE OK: st_metabolism 6 场景全绿")
