"""sc_pseudotime engine="monocle3" 容器冒烟（重启评估落地，断网）。

合成双分支库（trunk 100 细胞 t∈[0,0.5] → 命运 A/B 各 100 延伸
t→1，同 _smoke_sc_pseudotime 场景⑩/⑭ 注入纪律）：G_trunk_up 全程
升、G_fateA/G_fateB 仅各自命运后段升；leiden=trunk/A/B 人工分簇、
X_umap 借 PCA 前两维、neighbors 真算（宿主无 leidenalg/umap-learn
的壳惯例）。

断言：①monocle3+root_cluster=trunk：n_graph_edges≥2 + pt vs 真值
t2 rho≥0.8（mask NA）+ 三产物落盘 + obs monocle3_pseudotime 写回；
②start_cell 显式条码 → root_mode=explicit；③monocle3+
branch_top_n>0 → INVALID_INPUT（分支推断 palantir 专属纪律覆盖新
引擎）；④dyn_top_n=20 → monocle3_dyn_genes.csv 且 G_fateA/G_fateB
进 top_dyn。
"""
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import scanpy as sc
from scipy.sparse import csr_matrix
from scipy.stats import spearmanr

# 宿主 workspace/镜像：env 覆写（CI 冒烟用，与 settings 同口径）
WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", "I:/飞书agent/bio_workspace"))
DS = "scmonocle3smoke"
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]

rng = np.random.default_rng(42)
n_tr, n_f = 100, 100
t2 = np.concatenate([np.linspace(0, 0.5, n_tr),
                     np.linspace(0.5, 1, n_f),
                     np.linspace(0.5, 1, n_f)])
n2 = len(t2)
fate = np.array(["trunk"] * n_tr + ["A"] * n_f + ["B"] * n_f)
X = rng.poisson(2, (n2, 50)).astype(np.float32)
X[:, 0] = rng.poisson(t2 * 60 + 0.1, n2).astype(np.float32)
sig_a = np.where(fate == "A", (t2 - 0.5) * 2, 0.0)
sig_b = np.where(fate == "B", (t2 - 0.5) * 2, 0.0)
X[:, 1] = rng.poisson(sig_a * 60 + 0.1, n2).astype(np.float32)
X[:, 2] = rng.poisson(sig_b * 60 + 0.1, n2).astype(np.float32)
adata = sc.AnnData(csr_matrix(X))
adata.var_names = ["G_trunk_up", "G_fateA", "G_fateB"] + \
    [f"B{i}" for i in range(3, 50)]
sc.pp.normalize_total(adata)
sc.pp.log1p(adata)
adata.raw = adata
adata.var["highly_variable"] = True
sc.pp.pca(adata)
sc.pp.neighbors(adata)
adata.obs["leiden"] = pd.Categorical(fate)
adata.obsm["X_umap"] = adata.obsm["X_pca"][:, :2]
ds_dir = WS / DS
ds_dir.mkdir(parents=True, exist_ok=True)
adata.write_h5ad(ds_dir / "processed.h5ad")


def run_pt(**kw):
    """容器内跑 pseudotime.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": DS, **kw})
    r = subprocess.run(BASE + ["python", "/opt/sc_tools/pseudotime.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=1200)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


pt_dir = ds_dir / "pseudotime"

# ① root_cluster=trunk + dyn20：主图分支树 + pt 与真值强相关 + 写回
o1 = run_pt(engine="monocle3", root_cluster="trunk", dyn_top_n=20)
assert o1["ok"] and o1["method"] == "monocle3", o1
assert o1["root_mode"] == "cluster", o1
assert o1["n_graph_edges"] >= 2, o1
pt = pd.read_csv(pt_dir / "monocle3_pt.csv",
                 index_col=0)["monocle3_pseudotime"]
pt.index = pt.index.astype(str)  # csv 数字条码被解析为 int64
pt = pt.loc[[str(i) for i in range(n2)]]
mask = pt.notna().to_numpy()
assert mask.mean() > 0.95, f"NA 率过高: {1 - mask.mean():.2%}"
rho = float(spearmanr(pt.to_numpy()[mask], t2[mask]).statistic)
assert rho >= 0.8, f"monocle3 pt vs t2 rho={rho}"
for f in ("monocle3_pt.csv", "monocle3_graph.csv", "monocle3_umap.png",
          "monocle3_dyn_genes.csv"):
    assert (pt_dir / f).exists(), f
graph = pd.read_csv(pt_dir / "monocle3_graph.csv")
assert {"from", "to", "x1", "y1", "x2", "y2"} <= set(graph.columns), \
    graph.columns
ad_back = sc.read_h5ad(ds_dir / "processed.h5ad")
assert "monocle3_pseudotime" in ad_back.obs, ad_back.obs.columns
dyn = pd.read_csv(pt_dir / "monocle3_dyn_genes.csv")
assert {"G_fateA", "G_fateB"} <= set(dyn["gene"].head(20)), \
    sorted(dyn["gene"].head(10))

# ② start_cell 显式条码 → explicit 模式
o2 = run_pt(engine="monocle3", start_cell="0", dyn_top_n=0)
assert o2["ok"] and o2["root_mode"] == "explicit", o2
assert o2["root_cell_index"] == 0, o2

# ③ monocle3 + branch_top_n>0 → INVALID_INPUT（palantir 专属纪律）
bad = run_pt(engine="monocle3", branch_top_n=50)
assert not bad["ok"] and bad["error_code"] == "INVALID_INPUT", bad

print("SMOKE OK",
      f"| edges={o1['n_graph_edges']} rho={rho:.3f} "
      f"na={o1['n_na_pseudotime']}",
      "| dyn top 含 fateA/B"
      " | explicit root ok | branch_top_n rejected")
