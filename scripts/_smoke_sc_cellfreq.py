"""sc_cellfreq 容器冒烟（2026-09-13 命运偏向增强）：合成已知偏向库真跑（断网）。

宿主造 processed 壳（cellfreq 只用 obs，X 为占位单基因）：
200 细胞 × 3 簇，grp A/B 已知偏向——c1 偏 A（80/20）、c2 均衡
（50/50）、c3 偏 B（10/90）；by=sample（每 grp 内对半两样本）。
断言：①Fisher 显著簇恰为 c1/c3 且 higher_in 方向正确（c1→A、
c3→B）、fate_bias top 含两簇、roe_csv/roe_png 落盘、c2 不显著；
②三值列 group=tri → ok 仅卡方 + group_note 降级（无 fisher 键）。
BioRunner 调用约定：docker run --rm --network none -v <workspace>:/ws
<img> python /opt/sc_tools/cellfreq.py，stdin 传 args JSON。
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
DS = "sccellfreqsmoke"
IMG = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")
BASE = ["docker", "run", "--rm", "-i", "--network", "none",
        "-v", f"{str(WS).replace(chr(92), '/')}:/ws", IMG]

# 已知偏向注入：c1 偏 A、c2 均衡、c3 偏 B
ctype = (["c1"] * 100 + ["c2"] * 100 + ["c3"] * 100)
grp = (["A"] * 80 + ["B"] * 20 + ["A"] * 50 + ["B"] * 50
       + ["A"] * 10 + ["B"] * 90)
sample = [f"s{(i // 50) % 2 + 1}" for i in range(300)]
tri = ["x", "y", "z"] * 100
adata = sc.AnnData(csr_matrix(np.ones((300, 1), dtype=np.float32)))
adata.var_names = ["G0"]
adata.obs["sample"] = pd.Categorical(sample)
adata.obs["grp"] = pd.Categorical(grp)
adata.obs["ctype"] = pd.Categorical(ctype)
adata.obs["tri"] = pd.Categorical(tri)
ds_dir = WS / DS
ds_dir.mkdir(parents=True, exist_ok=True)
adata.write_h5ad(ds_dir / "processed.h5ad")


def run_cf(**kw):
    """容器内跑 cellfreq.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": DS, **kw})
    r = subprocess.run(BASE + ["python", "/opt/sc_tools/cellfreq.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=600)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 两组已知偏向：Fisher 显著簇=c1/c3、方向正确、产物落盘、c2 不显著
o = run_cf(by="sample", group="grp", celltype_col="ctype")
assert o["ok"] and o["roe_png"] and o["roe_csv"], o.keys()
tests = {t["cluster"]: t for t in o["chi2_tests"]}
assert tests["c1"]["fisher_q"] < 0.05, tests["c1"]
assert tests["c3"]["fisher_q"] < 0.05, tests["c3"]
assert tests["c2"]["fisher_q"] > 0.05, tests["c2"]
assert tests["c1"]["roe_A"] > 1 > tests["c1"]["roe_B"], tests["c1"]
assert tests["c3"]["roe_B"] > 1 > tests["c3"]["roe_A"], tests["c3"]
fb = {f["cluster"]: f["higher_in"] for f in o["fate_bias"]}
assert fb.get("c1") == "A" and fb.get("c3") == "B", fb
assert (ds_dir / "cellfreq" / "roe_by_grp.csv").exists()
assert (ds_dir / "cellfreq" / "cellfreq_roe.png").exists()

# ② 三值列 → 仅卡方降级 + group_note（无 fisher/roe 键）
o2 = run_cf(by="sample", group="tri", celltype_col="ctype")
assert o2["ok"] and o2["group_note"], o2.keys()
assert "chi-square only" in o2["group_note"], o2["group_note"]
assert o2["roe_png"] is None and o2["fate_bias"] is None
assert "fisher_q" not in o2["chi2_tests"][0], o2["chi2_tests"][0]

print("SMOKE OK",
      f"| fisher sig={sorted(fb)} direction ok",
      f"c1 roeA={tests['c1']['roe_A']}",
      f"c3 roeB={tests['c3']['roe_B']}",
      "| 3-level group degraded to chi2-only")
