"""st_stats 容器冒烟：合成网格数据三分析真跑（断网）。

合成 10×10 网格：左半簇 A 右半簇 B；G_grad 左高右低为注入空间模式
（Moran's I 应排第一），其余基因随机。BioRunner 调用约定：
docker run --rm --network none -v <workspace>:/ws <img> python
/opt/st_tools/stats.py，stdin 传 args JSON。
"""
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import scanpy as sc
from scipy.sparse import csr_matrix

# 宿主 workspace/镜像：env 覆写（CI 冒烟用，与 settings 同口径）
WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", "I:/飞书agent/bio_workspace"))
DS = "ststatssmoke"
rng = np.random.default_rng(42)

xs, ys = np.meshgrid(np.arange(10), np.arange(10))
coords = np.column_stack([xs.ravel(), ys.ravel()]).astype(float)
n = coords.shape[0]
left = (coords[:, 0] < 5).astype(np.float32)
X = rng.poisson(2, (n, 6)).astype(np.float32)
X[:, 0] += left * 8  # G_grad 空间梯度
adata = sc.AnnData(csr_matrix(X))
adata.var_names = ["G_grad", "G_rand", "G3", "G4", "G5", "G6"]
adata.obs["spatial_domain"] = pd.Categorical(np.where(left > 0, "A", "B"))
# 不对称三簇键（centrality top_hub 鉴别力）：A=60 格连通大块接壤最广
adata.obs["tri"] = pd.Categorical(np.where(
    coords[:, 0] < 6, "A", np.where(coords[:, 1] < 5, "B", "C")))
adata.obsm["spatial"] = coords
sc.pp.normalize_total(adata)
sc.pp.log1p(adata)
sc.pp.highly_variable_genes(adata, n_top_genes=6)

ds_dir = WS / DS
ds_dir.mkdir(parents=True, exist_ok=True)
adata.write_h5ad(ds_dir / "processed.h5ad")

IMG = os.environ.get("BIO_ST_IMAGE", "feishu-research-agent/bio:st-cpu-latest")
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]


def run_stats(**kw):
    """容器内跑 stats.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": DS, **kw})
    r = subprocess.run(BASE + ["python", "/opt/st_tools/stats.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=1200)
    # 脚本级 fail() 也是 exit 1 + stdout JSON（BioRunner 同款口径）；
    # 仅当 stdout 无法解析为 JSON 才算执行崩溃
    try:
        return json.loads(r.stdout)  # BioRunner 严格口径：整体解析
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


o1 = run_stats(analysis="autocorr", mode="moran")
assert o1["ok"] and o1["top_gene"] == "G_grad", o1
o1g = run_stats(analysis="autocorr", mode="geary")
assert o1g["ok"], o1g
o2 = run_stats(analysis="cooccurrence")
assert o2["ok"] and o2["n_clusters"] == 2, o2
o3 = run_stats(analysis="nhood_enrichment", n_perms=99)
# 两簇空间分离 → A~B 应为强耗竭（z << 0），|z| 显著即可
assert o3["ok"] and abs(o3["top_zscore"]) > 1 and o3["top_zscore"] < 0, o3
bad = run_stats(analysis="nope")
assert not bad["ok"] and bad["error_code"] == "INVALID_INPUT", bad
# Phase 46 centrality：默认两簇（对角主导）+ tri 三簇（A 接壤最广）
o4 = run_stats(analysis="centrality")
assert o4["ok"] and o4["n_clusters"] == 2, o4
im_df = pd.read_csv(ds_dir / "stats_centrality/interaction_matrix.csv",
                    index_col=0)
diag = float(np.diag(im_df.values).sum())
off = float(im_df.values.sum() - diag)
assert diag > off * 2, f"interaction 对角主导失败: diag={diag} off={off}"
o5 = run_stats(analysis="centrality", cluster_key="tri")
assert o5["ok"] and o5["top_hub"] == "A", o5
for f in ("stats_autocorr/autocorr_moran.csv",
          "stats_autocorr/autocorr_G_grad.png",
          "stats_autocorr/autocorr_geary.csv",
          "stats_cooccurrence/cooccurrence.csv",
          "stats_cooccurrence/cooccurrence.png",
          "stats_nhood_enrichment/nhood_zscore.csv",
          "stats_nhood_enrichment/nhood_count.csv",
          "stats_nhood_enrichment/nhood_enrichment.png",
          "stats_centrality/centrality_scores.csv",
          "stats_centrality/interaction_matrix.csv",
          "stats_centrality/centrality_degree.png",
          "stats_centrality/interaction_matrix.png"):
    assert (ds_dir / f).exists(), f
print("SMOKE OK",
      o1["top_gene"], round(o1["top_stat"], 3),
      "| geary top:", o1g["top_gene"], round(o1g["top_stat"], 3),
      "| cooc:", o2["n_clusters"],
      "| nhood:", o3["top_pair"], round(o3["top_zscore"], 2),
      "| centrality:", o4["top_hub"], "tri:", o5["top_hub"],
      "| invalid rejected")
