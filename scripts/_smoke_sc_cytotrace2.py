"""sc_cytotrace2 冒烟（CytoTRACE2 绝对干性，测试总结六补记死刑推翻
翻案落地）：合成 raw 计数库 + processed 壳（leiden/umap），容器断网跑。

场景：
①默认 mouse：跑通 + 五列写回 obs + by_cluster 汇总 + UMAP png +
  n_genes_mapped>0 + counts_source 定位 filtered.h5ad:X；
②species="human"：正交映射路径跑通；
③非计数库（仅 processed 且 X 为 scaled 浮点）→ INVALID_INPUT；
④species 非法 → INVALID_INPUT。

口径钉注：合成泊松噪声骗不过 19 模型 ensemble（六补记实证全
Differentiated 属预期）——本冒烟只断言链路结构（跑通/写回/产物/
拒收），不做生物学方向断言。
"""
import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get(
    "BIO_WORKSPACE_ROOT",
    Path(__file__).resolve().parents[1] / "bio_workspace"))
DS = "c2smoke"
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]

# 基因名取真实模型特征集（六补记：假名映射率崩 → fastCor nSplit=0；
# 特征集 csv 基因名在第二列且首行伪表头 `,0`）
FEAT_CSV = (WS / "_eval" / "_c2peek"
            / "digitalcytometry-cytotrace2-33b9c6b"
            / "cytotrace2_r" / "inst" / "extdata"
            / "features_model_training_17.csv")
feat = pd.read_csv(FEAT_CSV, header=None, skiprows=1)
gns = feat[1].astype(str)
gns = gns[gns.str.len() > 0].unique()

rng = np.random.default_rng(7)
n, ng = 1200, 800
genes = list(rng.choice(gns, ng, replace=False))
X = rng.poisson(2, (n, ng)).astype(np.float32)
t = np.linspace(0, 1, n)
X[:, 0] = rng.poisson(t * 40 + 1, n).astype(np.float32)  # 轻梯度（结构口径不作断言）

import anndata as ad  # noqa: E402

ds_dir = WS / DS
ds_dir.mkdir(parents=True, exist_ok=True)
# filtered.h5ad：整数计数（定位链首选）
a_raw = ad.AnnData(X=X.copy())
a_raw.var_names = genes
a_raw.obs_names = [f"c{i}" for i in range(n)]
a_raw.write_h5ad(ds_dir / "filtered.h5ad")
# processed 壳：scaled X（非计数）+ leiden 三段 + umap
a_p = ad.AnnData(X=(X - X.mean(0)) / (X.std(0) + 1e-9))
a_p.var_names = genes
a_p.obs_names = list(a_raw.obs_names)
a_p.obs["leiden"] = pd.Categorical(
    pd.cut(t, 3, labels=["0", "1", "2"]).astype(str))
a_p.obsm["X_umap"] = np.column_stack(
    [rng.normal(size=n), rng.normal(size=n)])
a_p.write_h5ad(ds_dir / "processed.h5ad")
# 非计数库（场景③）：仅 processed 且 X scaled
DS_NC = "c2smokenc"
ds_nc = WS / DS_NC
ds_nc.mkdir(parents=True, exist_ok=True)
a_p.write_h5ad(ds_nc / "processed.h5ad")


def run_c2(ds: str = DS, **kw):
    """容器内跑 cytotrace2.py（stdin JSON），严格口径整体解析 stdout。"""
    payload = json.dumps({"dataset_id": ds, **kw})
    r = subprocess.run(BASE + ["python", "/opt/sc_tools/cytotrace2.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=1500)
    try:
        return json.loads(r.stdout)  # BioRunner 严格口径：整体解析
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 默认 mouse：跑通 + 五列写回 + 汇总 + UMAP + 映射率
t0 = time.time()
o1 = run_c2()
assert o1["ok"], o1
assert o1["n_cells"] == n and o1["species"] == "mouse", o1
assert o1["counts_source"] == "filtered.h5ad:X", o1["counts_source"]
assert o1["n_genes_input"] == ng, o1
assert 0 < o1["n_genes_mapped"] <= ng, o1
assert o1["top5_clusters"], o1
aw = ad.read_h5ad(ds_dir / "processed.h5ad")
for col in ("CytoTRACE2_Score", "CytoTRACE2_Potency",
            "CytoTRACE2_Relative", "preKNN_CytoTRACE2_Score",
            "preKNN_CytoTRACE2_Potency"):
    assert col in aw.obs, aw.obs.columns
sc1 = aw.obs["CytoTRACE2_Score"].to_numpy(dtype=float)
assert np.isfinite(sc1).all() and sc1.min() >= 0 and sc1.max() <= 1, (
    sc1.min(), sc1.max())
legal_pot = {"Differentiated", "Unipotent", "Oligopotnent", "Oligopotent",
             "Multipotent", "Pluripotent", "Totipotent"}
pot_vals = set(aw.obs["CytoTRACE2_Potency"].astype(str))
assert pot_vals <= legal_pot, pot_vals
res_csv = pd.read_csv(ds_dir / "cytotrace2_result.csv", index_col=0)
assert list(res_csv.columns) == [
    "CytoTRACE2_Score", "CytoTRACE2_Potency", "CytoTRACE2_Relative",
    "preKNN_CytoTRACE2_Score", "preKNN_CytoTRACE2_Potency"], res_csv.columns
by = pd.read_csv(ds_dir / "cytotrace2_by_cluster.csv", index_col=0)
assert list(by.columns) == ["mean", "count"] and len(by) == 3, by
assert set(by.index.astype(str)) == {"0", "1", "2"}, by.index
assert (ds_dir / "cytotrace2_umap.png").exists()

# ② human：正交映射路径跑通（映射率允许衰减但链路完整）
o2 = run_c2(species="human")
assert o2["ok"] and o2["species"] == "human", o2

# ③ 非计数库 → INVALID_INPUT（定位链全不命中）
o3 = run_c2(DS_NC)
assert not o3["ok"] and o3["error_code"] == "INVALID_INPUT", o3
assert "counts" in o3["error_message"], o3

# ④ species 非法 → INVALID_INPUT
o4 = run_c2(species="zebrafish")
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4

wall = time.time() - t0
print(f"SMOKE OK | c2 {o1['n_cells']}c mapped={o1['n_genes_mapped']}/{ng}"
      f" pot={o1['potency_table'].get('Differentiated', 0)}diff"
      f" top={list(o1['top5_clusters'])[:2]}"
      f" | human ok | non-counts rejected | bad species rejected"
      f" | wall={wall:.0f}s")
