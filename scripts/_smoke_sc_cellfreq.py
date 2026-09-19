"""sc_cellfreq 容器冒烟（2026-09-13 命运偏向 + 2026-09-19 Phase 74 共现网络）：
合成已知偏向库/已知共现结构真跑（断网）。

宿主造 processed 壳（cellfreq 只用 obs，X 为占位单基因）：
200 细胞 × 3 簇，grp A/B 已知偏向——c1 偏 A（80/20）、c2 均衡
（50/50）、c3 偏 B（10/90）；by=sample（每 grp 内对半两样本）。
断言：①Fisher 显著簇恰为 c1/c3 且 higher_in 方向正确（c1→A、
c3→B）、fate_bias top 含两簇、roe_csv/roe_png 落盘、c2 不显著；
样本数 2<5 → cooccurrence=None + note 跳过提示；
②三值列 group=tri → ok 仅卡方 + group_note 降级（无 fisher 键）；
③Phase 74 共现主场景（4 簇 × 6 样本，c1/c2 同秩、c3/c4 近同秩、
组间完全/近反转）：n_edges=6、(c1,c2)/(c3,c4) rho>0.9、
(c1,c3) rho<-0.9、三产物落盘、note 含组成闭合警示；
④cooccurrence=false → cooccurrence=None 且无 note。
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

# Phase 74 共现场景：4 簇 × 6 样本，秩结构精准可控——n=6 下 Spearman
# 过 BH q<0.05 需 |rho|≳0.89，簇内只做保秩微调：c1/c2 逐样本同秩
# （rho=1.0）、c3/c4 近同秩（0.94）、组间完全/近反转（-1.0/-0.94）；
# 样本细胞总数按实际计数展开（611，不强凑整数）。
DS2 = "sccellfreqsmoke2"
_COUNTS2 = [(40, 41, 10, 9), (44, 43, 9, 10), (38, 39, 12, 12),
            (10, 10, 40, 41), (9, 9, 44, 43), (11, 11, 38, 38)]


def _grp(a: int, b: int, c: int, d: int) -> list[str]:
    """一个样本的簇构成（计数展开）。"""
    return ["c1"] * a + ["c2"] * b + ["c3"] * c + ["c4"] * d


ctype2 = sum((_grp(*row) for row in _COUNTS2), [])
sample2 = sum(([f"s{i}"] * sum(row) for i, row in enumerate(_COUNTS2)), [])
adata2 = sc.AnnData(csr_matrix(np.ones((len(ctype2), 1), dtype=np.float32)))
adata2.var_names = ["G0"]
adata2.obs["sample6"] = pd.Categorical(sample2)
adata2.obs["ctype2"] = pd.Categorical(ctype2)
ds_dir2 = WS / DS2
ds_dir2.mkdir(parents=True, exist_ok=True)
adata2.write_h5ad(ds_dir2 / "processed.h5ad")


def run_cf(**kw):
    """容器内跑 cellfreq.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": DS, **kw})
    r = subprocess.run(BASE + ["python", "/opt/sc_tools/cellfreq.py"],
                       input=payload, capture_output=True, text=True,
                       timeout=600)
    try:
        return json.loads(r.stdout)  # BioRunner 严格口径：整体解析
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n"
                         f"{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 两组已知偏向：Fisher 显著簇=c1/c3、方向正确、产物落盘、c2 不显著
o = run_cf(by="sample", group="grp", celltype_col="ctype")
assert o["ok"] and o["roe_png"] and o["roe_csv"], o.keys()
assert o["cooccurrence"] is None, o["cooccurrence"]  # 2 样本 < 5 功效线
assert o["cooccurrence_note"] and "5" in o["cooccurrence_note"], o["cooccurrence_note"]
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

# ③ Phase 74 共现主场景：6 样本 × 4 簇，已知 2 正 4 负高相关边
o3 = run_cf(dataset_id=DS2, by="sample6", celltype_col="ctype2")
co = o3["cooccurrence"]
assert o3["ok"] and co is not None, o3.keys()
assert co["n_samples"] == 6 and co["n_edges"] == 6, co
edges = {(e["source"], e["target"]): e["rho"] for e in
         [{"source": r["source"], "target": r["target"], "rho": r["rho"]}
          for r in pd.read_csv(WS / Path(co["edges_csv"]).relative_to("/ws")).to_dict("records")]}
def _pair(a, b):
    """无向对键（边表按字母序 source<target 存）。"""
    return (a, b) if a < b else (b, a)
assert edges[_pair("c1", "c2")] > 0.9, edges
assert edges[_pair("c3", "c4")] > 0.9, edges
assert edges[_pair("c1", "c3")] < -0.9, edges
assert "闭合" in co["note"], co["note"]
assert len(co["communities"]) == 4 and co["n_communities"] >= 1, co
for key in ("edges_csv", "communities_csv", "network_png"):
    assert (WS / Path(co[key]).relative_to("/ws")).exists(), co[key]
assert co["top_edges"], co

# ④ cooccurrence=false → 显式关闭无 note
o4 = run_cf(dataset_id=DS2, by="sample6", celltype_col="ctype2", cooccurrence=False)
assert o4["ok"] and o4["cooccurrence"] is None
assert o4["cooccurrence_note"] is None, o4["cooccurrence_note"]

print("SMOKE OK",
      f"| fisher sig={sorted(fb)} direction ok",
      f"c1 roeA={tests['c1']['roe_A']}",
      f"c3 roeB={tests['c3']['roe_B']}",
      "| 3-level group degraded to chi2-only",
      f"| cooc n_edges={co['n_edges']} n_comm={co['n_communities']}",
      f"top={co['top_edges'][0]['source']}-{co['top_edges'][0]['target']}",
      f"rho={co['top_edges'][0]['rho']}",
      "| off-switch silent OK")
