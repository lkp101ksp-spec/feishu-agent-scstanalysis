"""sc_metabolism 冒烟（Phase 73 AUCell + mean 双口径，spec
2026-09-19-phase72-74-genescore-metabolism-composition-design.md §5）：
镜像 KEGG 快照真值注入 + 容器断网跑。

场景：
①method=aucell 主场景：A 簇注入 2.5×Glycolysis 全量基因信号 → ok +
  method=aucell + n_pathways_scored>=300 + 三产物落盘 + method_note
  含 AUCell；
②csv 内容：Glycolysis 簇间方差 rank<=3 且 A 均值 > B 均值×3、
  top_pathways 前 3 含 Glycolysis；
③method=mean 回归：同数据 score_genes 口径 Glycolysis 仍 A>B 分离
  （A 均值>1.5，z-score 尺度）；
④method=bad → SCRIPT_ERROR 且 message 含 method 提示；
⑤groupby=patho_anno 自定义列：ok + groupby 回显 + 两份 csv 首列跟随；
⑥groupby=not_a_col → SCRIPT_ERROR 且 message 含列名提示。

合成 h5ad：KEGG 全通路靶基因并集（>=500）+ 200 噪声基因 × 80 细胞
（leiden A/B 各 40），N(6,0.8) 基底（seed=7），A 簇糖酵解基因 +2.5；
raw=X 同层（metabolism.py 走 raw=True/use_raw 分支，与 sc_process
产物一致）。通路表从镜像 /opt/gene_sets/kegg.json 动态导出（不硬编码）。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", Path(__file__).resolve().parents[1] / "bio_workspace"))
DATA = WS / "_smoke_metabolism_data"
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

# KEGG 库从镜像导出（宿主无 /opt/gene_sets，不硬编码通路表）
DUMP_CMD = (
    "import shutil; "
    "shutil.copy('/opt/gene_sets/kegg.json', "
    "'/ws/_smoke_metabolism_data/kegg.json')"
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


def build_gex(ds: str) -> str:
    """合成 processed.h5ad：UNIVERSE+噪声 × 80 细胞（leiden A/B 各 40）；
    A 簇糖酵解基因 +2.5（seed=7），raw=X 同层。"""
    import anndata as ad

    rng = np.random.default_rng(7)
    n_a, n_b = 40, 40
    genes = UNIVERSE + [f"NOISE{i:04d}" for i in range(200)]
    x = rng.normal(6.0, 0.8, (n_a + n_b, len(genes))).astype(np.float32)
    gly_idx = [genes.index(g) for g in gly_genes]
    x[:n_a, gly_idx] += 2.5
    a = ad.AnnData(X=x)
    a.var_names = genes
    a.obs_names = [f"A{i:02d}" for i in range(n_a)] + [f"B{i:02d}" for i in range(n_b)]
    a.obs["leiden"] = pd.Categorical(["A"] * n_a + ["B"] * n_b)
    a.obs["patho_anno"] = ["a"] * n_a + ["b"] * n_b
    a.raw = a
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "processed.h5ad")
    return ds


DS_MAIN = build_gex("_smoke_metabolism_gex")


def run_metab(**kw):
    """容器内跑 metabolism.py（stdin JSON），整体解析 stdout。"""
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/metabolism.py"],
        input=json.dumps(kw),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 主场景：AUCell 打分 + 三产物落盘
o1 = run_metab(dataset_id=DS_MAIN, top_n=30, species="human", method="aucell")
assert o1["ok"], o1
assert o1["method"] == "aucell", o1
assert "AUCell" in o1["method_note"], o1["method_note"]
assert o1["n_cells"] == 80, o1
assert o1["n_pathways_scored"] >= 300, o1["n_pathways_scored"]
for key in ("scores_csv", "cluster_mean_csv", "heatmap_png"):
    assert (WS / Path(o1[key]).relative_to("/ws")).exists(), o1[key]
print(f"① AUCell：{o1['n_pathways_scored']} 通路 + 三产物落盘 OK"
      f"（umap={o1['umap_png'] is None} 合成数据无 X_umap 属预期）")

# ② csv 内容：Glycolysis 方差 rank<=3 且 A 均值 > B×3
gm = pd.read_csv(WS / Path(o1["cluster_mean_csv"]).relative_to("/ws"), index_col=0)
gly_a, gly_b = float(gm.loc["A", gly_key]), float(gm.loc["B", gly_key])
tp = [d["term"] for d in o1["top_pathways"]]
assert tp.index(gly_key) <= 2, tp[:5]
assert gly_a > gly_b * 3.0, (gly_a, gly_b)
sc = pd.read_csv(WS / Path(o1["scores_csv"]).relative_to("/ws"))
assert len(sc) == 80 and gly_key in sc.columns, (sc.shape, sc.columns[:3])
print(
    f"② csv：{gly_key}@A={gly_a:.4f} > B={gly_b:.4f}×3 + 方差 rank="
    f"{tp.index(gly_key) + 1} + 80 行含通路列 OK"
)

# ③ method=mean 回归：score_genes 口径 Glycolysis 仍分离
o3 = run_metab(dataset_id=DS_MAIN, top_n=30, species="human", method="mean")
assert o3["ok"] and o3["method"] == "mean", o3
gm3 = pd.read_csv(WS / Path(o3["cluster_mean_csv"]).relative_to("/ws"), index_col=0)
gly_a3, gly_b3 = float(gm3.loc["A", gly_key]), float(gm3.loc["B", gly_key])
assert gly_a3 > 1.5 and gly_a3 > gly_b3, (gly_a3, gly_b3)
assert o3["n_pathways_scored"] >= 300, o3["n_pathways_scored"]
print(f"③ mean 回归：score_genes A={gly_a3:.3f} > B={gly_b3:.3f} OK")

# ④ method 非法 → SCRIPT_ERROR（message 含 method 提示）
o4 = run_metab(dataset_id=DS_MAIN, top_n=30, species="human", method="bad")
assert not o4["ok"] and o4["error_code"] == "SCRIPT_ERROR", o4
assert "method" in o4["error_message"], o4["error_message"]
print("④ method=bad SCRIPT_ERROR + 提示 OK")

# ⑤ groupby 自定义列：回显 + 两份 csv 首列跟随自定义名
o5 = run_metab(dataset_id=DS_MAIN, top_n=5, species="human",
               method="aucell", groupby="patho_anno")
assert o5["ok"] and o5["groupby"] == "patho_anno", o5
gm5 = pd.read_csv(WS / Path(o5["cluster_mean_csv"]).relative_to("/ws"))
assert gm5.columns[0] == "patho_anno", gm5.columns[:2]
sc5 = pd.read_csv(WS / Path(o5["scores_csv"]).relative_to("/ws"), nrows=1)
assert "patho_anno" in list(sc5.columns[:2]), sc5.columns[:3]
print("⑤ groupby=patho_anno：回显 + csv 首列跟随 OK")

# ⑥ groupby 列不存在 → SCRIPT_ERROR（message 含列名提示）
o6 = run_metab(dataset_id=DS_MAIN, top_n=5, species="human",
               method="aucell", groupby="not_a_col")
assert not o6["ok"] and o6["error_code"] == "SCRIPT_ERROR", o6
assert "not_a_col" in o6["error_message"], o6["error_message"]
print("⑥ groupby=not_a_col SCRIPT_ERROR + 列名提示 OK")

print("\nSMOKE OK: sc_metabolism 6 场景全绿")
