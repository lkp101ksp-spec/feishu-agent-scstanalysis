"""sc_genescore 冒烟（Phase 72 PROGENy 通路活性，spec
2026-09-19-phase72-74-genescore-metabolism-composition-design.md §6）：
镜像模型快照真值注入 + 容器断网跑。

场景：
①主场景：A 群注入 2.0×TGFb top100 权重向量、B 群纯噪声 → ok +
  n_pathways_scored=14 + dropped 空 + 三产物落盘；
②csv 内容：TGFb@A 组均值 top3 且 > B 群 ×3、组间方差 rank≤3、
  scores csv 行数=细胞数且含 groupby 列；
③低重叠拒收（600 FAKE 基因）→ GENESCORE_LOW_OVERLAP；
④groupby 缺列 → INVALID_INPUT；
⑤species=mouse → INVALID_INPUT（PROGENy human-only）。

合成 h5ad：14 通路各 top50 靶基因并集 + 200 噪声基因 × 40 细胞
（A/B 各 20），N(6,0.8) log 形态基底（seed=7，无 raw 层→测 X 分支）；
通路表从镜像 /opt/progeny/progeny_human_top500.tsv 动态导出（不硬编码）。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", Path(__file__).resolve().parents[1] / "bio_workspace"))
DATA = WS / "_smoke_genescore_data"
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

# 全模型 TSV 从镜像导出（宿主无 /opt/progeny，不硬编码通路表）
DUMP_CMD = (
    "import pandas as pd; "
    "net = pd.read_csv('/opt/progeny/progeny_human_top500.tsv', "
    "sep='\\t'); "
    "net.to_csv('/ws/_smoke_genescore_data/progeny_full.csv', "
    "index=False)"
)

DATA.mkdir(parents=True, exist_ok=True)

r0 = subprocess.run(BASE + ["python", "-c", DUMP_CMD], capture_output=True, text=True, timeout=300)
if r0.returncode != 0:
    raise SystemExit(f"model dump rc={r0.returncode}\n{r0.stderr[-600:]}")
net = pd.read_csv(DATA / "progeny_full.csv", sep=None, engine="python")
assert net["source"].nunique() == 14, net["source"].unique()

# 14 通路各 top50 靶基因并集（全部在数据里 → tmin=5 全通过）+ TGFb 注入向量
top50 = net.sort_values("weight", ascending=False).groupby("source", sort=False).head(50)
tgfb = net[net["source"] == "TGFb"].nlargest(100, "weight")
tgfb_w = dict(zip(tgfb["target"], tgfb["weight"]))
UNIVERSE = sorted(set(top50["target"]) | set(tgfb_w))
assert len(UNIVERSE) >= 500, len(UNIVERSE)


def build_gex(ds: str, genes: list[str], inject_a: bool) -> str:
    """合成 processed.h5ad：genes × 40 细胞（A/B 各 20）；inject_a 时
    A 群加 2.0×TGFb top100 权重向量（seed=7）。"""
    import anndata as ad

    rng = np.random.default_rng(7)
    n_a, n_b = 20, 20
    x = rng.normal(6.0, 0.8, (n_a + n_b, len(genes))).astype(np.float32)
    if inject_a:
        sig_vec = np.array([tgfb_w.get(g, 0.0) for g in genes], dtype=np.float32)
        x[:n_a] += 2.0 * sig_vec
    a = ad.AnnData(X=x)
    a.var_names = genes
    a.obs_names = [f"A{i:02d}" for i in range(n_a)] + [f"B{i:02d}" for i in range(n_b)]
    a.obs["celltype"] = ["A"] * n_a + ["B"] * n_b
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "processed.h5ad")
    return ds


DS_MAIN = build_gex("_smoke_genescore_gex", UNIVERSE + [f"NOISE{i:04d}" for i in range(200)], True)
DS_LOWOV = build_gex("_smoke_genescore_lowov", [f"FAKE{i:05d}" for i in range(600)], False)


def run_genescore(**kw):
    """容器内跑 genescore.py（stdin JSON），整体解析 stdout。"""
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/genescore.py"],
        input=json.dumps(kw),
        capture_output=True,
        text=True,
        timeout=1200,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 主场景：14 通路全打分 + 三产物落盘
o1 = run_genescore(dataset_id=DS_MAIN, groupby="celltype")
assert o1["ok"], o1
assert o1["method"] == "progeny_mlm" and o1["n_cells"] == 40, o1
assert o1["n_pathways_total"] == 14, o1
assert o1["n_pathways_scored"] == 14, o1
assert o1["dropped_pathways"] == [], o1["dropped_pathways"]
for key in ("scores_csv", "group_mean_csv", "heatmap_png"):
    assert (WS / Path(o1[key]).relative_to("/ws")).exists(), o1[key]
print(f"① 主场景：14/14 通路 + 三产物落盘 OK（umap={o1['umap_png'] is None} 合成数据无 X_umap 属预期）")

# ② csv 内容：TGFb@A top3 且 > B×3；组间方差 rank≤3；scores 行数对齐
gm = pd.read_csv(WS / Path(o1["group_mean_csv"]).relative_to("/ws"), index_col=0)
tgfb_a, tgfb_b = float(gm.loc["A", "TGFb"]), float(gm.loc["B", "TGFb"])
assert (
    gm.loc["A"].idxmax() == "TGFb" or gm.loc["A"].sort_values(ascending=False).index.get_loc("TGFb") <= 2
), gm.loc["A"].sort_values(ascending=False).head(5)
assert tgfb_a > tgfb_b * 3.0, (tgfb_a, tgfb_b)
tp = [d["pathway"] for d in o1["top_pathways"]]
assert "TGFb" in tp[:3], tp
sc = pd.read_csv(WS / Path(o1["scores_csv"]).relative_to("/ws"))
assert len(sc) == 40 and "celltype" in sc.columns, sc.shape
print(
    f"② csv：TGFb@A={tgfb_a:.3f} > B={tgfb_b:.3f}×3 + 方差 rank="
    f"{tp.index('TGFb') + 1} + 40 行含 groupby 列 OK"
)

# ③ 低重叠拒收（600 FAKE 基因 < 100 交集下限的靶基因）
o3 = run_genescore(dataset_id=DS_LOWOV, groupby="celltype")
assert not o3["ok"] and o3["error_code"] == "GENESCORE_LOW_OVERLAP", o3
print("③ 低重叠 GENESCORE_LOW_OVERLAP OK")

# ④ groupby 缺列拒收（带候选列提示）
o4 = run_genescore(dataset_id=DS_MAIN, groupby="not_a_col")
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4
assert "celltype" in o4["error_message"], o4
print("④ groupby 缺列 INVALID_INPUT + 候选提示 OK")

# ⑤ species=mouse 拒收（PROGENy human-only）
o5 = run_genescore(dataset_id=DS_MAIN, groupby="celltype", species="mouse")
assert not o5["ok"] and o5["error_code"] == "INVALID_INPUT", o5
assert "human" in o5["error_message"], o5
print("⑤ species=mouse INVALID_INPUT OK")

print("\nSMOKE OK: sc_genescore 5 场景全绿")
