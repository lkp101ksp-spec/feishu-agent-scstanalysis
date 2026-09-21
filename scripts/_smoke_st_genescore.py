"""st_genescore 冒烟（Phase 75 空间版，spec
2026-09-20-phase75-crossread-stgenescore-stmetabolism-design.md §3.5）：
空间网格 + spatial_domain 真值注入 + 容器断网跑。

场景：
①主场景：D1 域（网格左半）注入 2.0×TGFb top100 权重向量 → ok +
  n_pathways_scored=14 + n_domains=2 + 四产物落盘（含 spatial png）；
②csv 内容：TGFb@D1 组均值 idxmax 且 > D2×3、scores csv 行数=spot 数
  且含 spatial_domain 列、top_by_group 含 TGFb；
③低重叠拒收（600 FAKE 基因）→ GENESCORE_LOW_OVERLAP；
④groupby 缺列 → INVALID_INPUT + 候选列提示；
⑤无 obsm.spatial → INVALID_INPUT + 引导 sc_genescore（st 门槛）；
⑥单域 groupby → INVALID_INPUT（<2 域无组间方差）。

合成 h5ad：14 通路各 top50 靶基因并集 + 200 噪声基因 × 40 spot（8×5
网格，左半 D1/右半 D2 空间连续），N(6,0.8) 基底（seed=7，无 raw 层→
测 X 分支）；通路表从镜像动态导出（不硬编码）。
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

WS = Path(os.environ.get("BIO_WORKSPACE_ROOT", Path(__file__).resolve().parents[1] / "bio_workspace"))
DATA = WS / "_smoke_st_genescore_data"
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

DUMP_CMD = (
    "import pandas as pd; "
    "net = pd.read_csv('/opt/progeny/progeny_human_top500.tsv', "
    "sep='\\t'); "
    "net.to_csv('/ws/_smoke_st_genescore_data/progeny_full.csv', "
    "index=False)"
)

DATA.mkdir(parents=True, exist_ok=True)

r0 = subprocess.run(BASE + ["python", "-c", DUMP_CMD], capture_output=True, text=True, timeout=300)
if r0.returncode != 0:
    raise SystemExit(f"model dump rc={r0.returncode}\n{r0.stderr[-600:]}")
net = pd.read_csv(DATA / "progeny_full.csv", sep=None, engine="python")
assert net["source"].nunique() == 14, net["source"].unique()

top50 = net.sort_values("weight", ascending=False).groupby("source", sort=False).head(50)
tgfb = net[net["source"] == "TGFb"].nlargest(100, "weight")
tgfb_w = dict(zip(tgfb["target"], tgfb["weight"]))
UNIVERSE = sorted(set(top50["target"]) | set(tgfb_w))
assert len(UNIVERSE) >= 500, len(UNIVERSE)


def build_gex(ds: str, genes: list[str], inject_d1: bool,
              spatial: bool = True, single_domain: bool = False) -> str:
    """合成 processed.h5ad：genes × 40 spot（8×5 网格左半 D1/右半 D2）；
    inject_d1 时 D1 加 2.0×TGFb top100 权重向量（seed=7）。"""
    import anndata as ad

    rng = np.random.default_rng(7)
    n = 40
    cols = np.arange(n) % 8
    rows = np.arange(n) // 8
    dom = np.where(cols < 4, "D1", "D2")
    x = rng.normal(6.0, 0.8, (n, len(genes))).astype(np.float32)
    if inject_d1:
        sig_vec = np.array([tgfb_w.get(g, 0.0) for g in genes], dtype=np.float32)
        x[dom == "D1"] += 2.0 * sig_vec
    a = ad.AnnData(X=x)
    a.var_names = genes
    a.obs_names = [f"S{i:02d}" for i in range(n)]
    a.obs["spatial_domain"] = pd.Categorical(
        ["D1"] * n if single_domain else dom.tolist())
    if spatial:
        a.obsm["spatial"] = np.column_stack([cols, rows]).astype(np.float64)
    d = WS / ds
    d.mkdir(parents=True, exist_ok=True)
    a.write_h5ad(d / "processed.h5ad")
    return ds


DS_MAIN = build_gex("_smoke_st_genescore_gex", UNIVERSE + [f"NOISE{i:04d}" for i in range(200)], True)
DS_LOWOV = build_gex("_smoke_st_genescore_lowov", [f"FAKE{i:05d}" for i in range(600)], False)
DS_NOSPAT = build_gex("_smoke_st_genescore_nospat", UNIVERSE, False, spatial=False)
DS_1DOM = build_gex("_smoke_st_genescore_1dom", UNIVERSE, False, single_domain=True)


def run_gs(**kw):
    """容器内跑 st_genescore.py（stdin JSON），整体解析 stdout。"""
    r = subprocess.run(
        BASE + ["python", "/opt/sc_tools/st_genescore.py"],
        input=json.dumps(kw),
        capture_output=True,
        text=True,
        timeout=1200,
    )
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(f"{kw} rc={r.returncode}\n{r.stdout[-400:]}\n{r.stderr[-600:]}") from None


# ① 主场景：14 通路全打分 + 四产物落盘（含空间图）
o1 = run_gs(dataset_id=DS_MAIN)
assert o1["ok"], o1
assert o1["method"] == "progeny_mlm" and o1["n_spots"] == 40, o1
assert o1["n_pathways_scored"] == 14, o1
assert o1["n_domains"] == 2, o1
assert o1["dropped_pathways"] == [], o1["dropped_pathways"]
for key in ("scores_csv", "group_mean_csv", "heatmap_png", "spatial_png"):
    assert (WS / Path(o1["products"][key]).relative_to("/ws")).exists(), o1["products"][key]
assert o1["products"]["umap_png"] is None  # 合成数据无 X_umap 属预期
assert "TGFb" in o1["top_by_group"]["D1"][:3], o1["top_by_group"]
print("① 主场景：14/14 通路 + 四产物落盘（含 spatial png）OK")

# ② csv 内容：TGFb@D1 idxmax 且 > D2×3；行数对齐 + 含 spatial_domain 列
gm = pd.read_csv(WS / Path(o1["products"]["group_mean_csv"]).relative_to("/ws"), index_col=0)
tgfb_d1, tgfb_d2 = float(gm.loc["D1", "TGFb"]), float(gm.loc["D2", "TGFb"])
assert gm.loc["D1"].idxmax() == "TGFb", gm.loc["D1"].sort_values(ascending=False).head(5)
assert tgfb_d1 > tgfb_d2 * 3.0, (tgfb_d1, tgfb_d2)
sc = pd.read_csv(WS / Path(o1["products"]["scores_csv"]).relative_to("/ws"))
assert len(sc) == 40 and "spatial_domain" in sc.columns, sc.shape
print(f"② csv：TGFb@D1={tgfb_d1:.3f} > D2={tgfb_d2:.3f}×3 + 40 行含 spatial_domain 列 OK")

# ③ 低重叠拒收
o3 = run_gs(dataset_id=DS_LOWOV)
assert not o3["ok"] and o3["error_code"] == "GENESCORE_LOW_OVERLAP", o3
print("③ 低重叠 GENESCORE_LOW_OVERLAP OK")

# ④ groupby 缺列拒收（带候选列提示）
o4 = run_gs(dataset_id=DS_MAIN, groupby="not_a_col")
assert not o4["ok"] and o4["error_code"] == "INVALID_INPUT", o4
assert "spatial_domain" in o4["error_message"], o4
print("④ groupby 缺列 INVALID_INPUT + 候选提示 OK")

# ⑤ 无 obsm.spatial 拒收（st 门槛，引导 sc_genescore）
o5 = run_gs(dataset_id=DS_NOSPAT)
assert not o5["ok"] and o5["error_code"] == "INVALID_INPUT", o5
assert "spatial" in o5["error_message"] and "sc_genescore" in o5["error_message"], o5
print("⑤ 无 obsm.spatial INVALID_INPUT + 引导 sc_genescore OK")

# ⑥ 单域 groupby 拒收
o6 = run_gs(dataset_id=DS_1DOM)
assert not o6["ok"] and o6["error_code"] == "INVALID_INPUT", o6
assert "1 个域" in o6["error_message"], o6
print("⑥ 单域 INVALID_INPUT + 换列引导 OK")

print("\nSMOKE OK: st_genescore 6 场景全绿")
