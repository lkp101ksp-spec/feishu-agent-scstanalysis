"""sc_pseudotime 容器冒烟（Phase 53）：合成 1D 分化梯度真跑（断网）。

宿主 scanpy 造 processed 壳（复用 _smoke_st_stats 宿主建库模式，
pseudotime 不依赖资源库无需容器内建库）：300 细胞 1D 梯度，
G_up 表达∝t、G_down∝(1-t)，其余 198 基因随机；scanpy 全链
（normalize/log1p/HVG/PCA/neighbors/raw）补 processed 三要件
（leiden=梯度三段人工分簇、X_umap 借 PCA 前两维、neighbors 真算；
宿主无 leidenalg/umap-learn，壳只需三要件存在）。
BioRunner 调用约定：docker run --rm --network none -v <workspace>:/ws
<img> python /opt/sc_tools/pseudotime.py，stdin 传 args JSON。

断言：①默认调用 G_up/G_down 进 top_dyn 且三产物落盘；
②root_cluster=众数簇 → root_mode=cluster 且 root 属该簇；
③root_marker+root_cluster 同给 → INVALID_INPUT；
④dyn_top_n=0 → ok 且无 dyn 键（Phase 32 现状兼容）。
"""
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import scanpy as sc
from scipy.sparse import csr_matrix

WS = Path("I:/飞书agent/bio_workspace")
DS = "scpseudotimesmoke"
IMG = "feishu-research-agent/bio:cpu-latest"
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]

# 宿主迭代实测：加法式注入（t*12 叠加于 198 背景）信号被
# normalize_total 稀释（DPT rho 仅 0.25）；替换式 amp=60/48 背景
# → rho≈0.90（DPT 扩散量化上限），top2 基因必为 G_up/G_down
rng = np.random.default_rng(42)
n, ng = 300, 50
t = np.linspace(0, 1, n)
X = rng.poisson(2, (n, ng)).astype(np.float32)
X[:, 0] = rng.poisson(t * 60 + 0.1, n).astype(np.float32)       # G_up
X[:, 1] = rng.poisson((1 - t) * 60 + 0.1, n).astype(np.float32)  # G_down
adata = sc.AnnData(csr_matrix(X))
adata.var_names = ["G_up", "G_down"] + [f"G{i}" for i in range(2, ng)]
sc.pp.normalize_total(adata)
sc.pp.log1p(adata)
adata.raw = adata
sc.pp.highly_variable_genes(adata, n_top_genes=100)
sc.pp.pca(adata)
sc.pp.neighbors(adata)
# 宿主无 leidenalg/umap-learn：processed 壳只需三要件存在——
# leiden 用梯度三段人工分簇（确定性更强），X_umap 借 PCA 前两维
adata.obs["leiden"] = pd.Categorical(
    pd.cut(t, 3, labels=["0", "1", "2"]).astype(str))
adata.obsm["X_umap"] = adata.obsm["X_pca"][:, :2]

ds_dir = WS / DS
ds_dir.mkdir(parents=True, exist_ok=True)
adata.write_h5ad(ds_dir / "processed.h5ad")
modal_cluster = adata.obs["leiden"].value_counts().index[0]


def run_pt(**kw):
    """容器内跑 pseudotime.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": DS, **kw})
    r = subprocess.run(BASE + ["python", "/opt/sc_tools/pseudotime.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=1200)
    # 脚本级 fail() 也是 stdout JSON（BioRunner 同款口径）；
    # 仅当 stdout 无法解析为 JSON 才算执行崩溃
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 默认（root_marker 空 + dyn_top_n=50）：dyn top2 必为注入梯度基因
o1 = run_pt()
assert o1["ok"] and o1["root_mode"] == "fallback", o1
top2 = o1["top_dyn"][:2]
top_genes = {d["gene"] for d in top2}
assert top_genes == {"G_up", "G_down"}, f"top2 非梯度基因: {top2}"
rhos = {d["gene"]: d["rho"] for d in top2}
assert rhos["G_up"] > 0.8 and rhos["G_down"] < -0.8, rhos
assert o1["n_dyn"] >= 2, o1["n_dyn"]

# ② root_cluster=众数簇：cluster 模式定根且 root 属该簇
o2 = run_pt(root_cluster=modal_cluster)
assert o2["ok"] and o2["root_mode"] == "cluster", o2
pt_df = pd.read_csv(ds_dir / "pseudotime/pseudotime.csv", index_col=0)
pt_df.index = pt_df.index.astype(str)  # csv 数字条码被解析为 int64
root_cell = pt_df.index[o2["root_cell_index"]]
assert adata.obs.loc[root_cell, "leiden"] == modal_cluster, (
    f"root {root_cell} 不属簇 {modal_cluster}")
assert f"cluster {modal_cluster}" in o2["root_note"], o2["root_note"]

# ③ root_marker+root_cluster 同给 → INVALID_INPUT
bad = run_pt(root_marker="G_up", root_cluster=modal_cluster)
assert not bad["ok"] and bad["error_code"] == "INVALID_INPUT", bad

# ④ dyn_top_n=0 → ok 且无 dyn 键（Phase 32 现状兼容）
o4 = run_pt(dyn_top_n=0, root_marker="G_up")
assert o4["ok"] and o4["root_mode"] == "marker", o4
assert "top_dyn" not in o4 and "dyn_csv" not in o4, o4.keys()

for f in ("pseudotime/pseudotime.csv",
          "pseudotime/pseudotime_umap.png",
          "pseudotime/paga_graph.png",
          "pseudotime/dyn_genes.csv",
          "pseudotime/trend_heatmap.png",
          "pseudotime/trend_curves.png"):
    assert (ds_dir / f).exists(), f
print("SMOKE OK",
      "| dyn top:", sorted(top_genes)[:4],
      f"G_up rho={rhos['G_up']:.3f} G_down rho={rhos['G_down']:.3f}",
      f"n_dyn={o1['n_dyn']}",
      "| cluster root:", o2["root_note"],
      "| mutex rejected | dyn_top_n=0 compat")
