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
④dyn_top_n=0 → ok 且无 dyn 键（Phase 32 现状兼容）；
⑤engine=palantir：pt vs 真值 t 的 rho>0.9（根 cell#0=t≈0 端
  方向必正）+ n_terminal≥1 + palantir 三产物落盘；
⑥engine=palantir + root_cluster → root_mode=cluster；
⑦engine=palantir + start_cell 显式条码 → root_mode=explicit；
⑧start_cell 非法条码 / ⑨engine 非法值 → INVALID_INPUT；
⑩分支推断（BEAM-lite，独立双分支库）：trunk 100 细胞 t∈[0,0.5]
  后分命运 A/B 各 100 延伸 t→1，G_trunk_up 全程升、G_fateA/G_fateB
  仅各自命运后段升（替换式 amp=60 同纪律）→ palantir+branch_top_n
  =50：归属率>0.5 + 分支 DE top 含 G_fateA/G_fateB 且 higher_in
  方向正确（归属细胞≥70% 来自对应人工命运簇）+ 四产物落盘；
⑪dpt+branch_top_n>0 → INVALID_INPUT；
⑭slingshot 引擎（同双分支库，root_cluster=trunk）：n_lineages≥2
  + 主 pt vs 真值 t2 rho≥0.8 + 三产物 + obs 写回 + 谱系×分支交叉
  自动触发（⑩已写 palantir_branch，Fisher 三列+sig≥1）；
⑮slingshot+branch_top_n>0 → INVALID_INPUT；
⑰dpt+paga=1：n_paga_edges≥1 + paga_graph.csv/paga_umap.png；
⑱palantir+branch50+paga+paga_pt 组合：交叉反向触发（triggered_by=
  palantir）+paga 双产物+obs paga_dpt_pseudotime 齐备；paga_pt 无
  paga → INVALID_INPUT。
"""
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import scanpy as sc
from scipy.sparse import csr_matrix
from scipy.stats import spearmanr

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


def run_pt(ds: str = DS, **kw):
    """容器内跑 pseudotime.py（stdin JSON），返回 emit 的结果 dict。"""
    payload = json.dumps({"dataset_id": ds, **kw})
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

# ⑤ engine=palantir 默认：pt vs 真值 t 强正相关 + 终末态 + 三产物
o5 = run_pt(engine="palantir")
assert o5["ok"] and o5["method"] == "palantir", o5
assert o5["root_mode"] == "fallback" and o5["root_cell_index"] == 0, o5
assert o5["n_terminal"] >= 1, o5
pal_pt = pd.read_csv(ds_dir / "pseudotime/palantir_pt.csv",
                     index_col=0)["palantir_pseudotime"]
pal_pt.index = pal_pt.index.astype(str)
rho_pal = float(spearmanr(pal_pt.loc[[str(i) for i in range(n)]],
                          t).statistic)
assert rho_pal > 0.9, f"palantir pt vs t rho={rho_pal}"
for f in ("pseudotime/palantir_pt.csv",
          "pseudotime/terminal_states.csv",
          "pseudotime/palantir_umap.png",
          "pseudotime/palantir_branch_umap.png"):
    assert (ds_dir / f).exists(), f
assert o5["branch_umap_png"].endswith("palantir_branch_umap.png"), o5
assert (ds_dir / "pseudotime/palantir_branch_umap.png"
        ).stat().st_size > 0

# ⑥ palantir + root_cluster → cluster 模式定根
o6 = run_pt(engine="palantir", root_cluster=modal_cluster)
assert o6["ok"] and o6["root_mode"] == "cluster", o6
assert f"cluster {modal_cluster}" in o6["root_note"], o6["root_note"]

# ⑦ palantir + start_cell 显式条码 → explicit 模式（条码 "0"=t≈0 端）
o7 = run_pt(engine="palantir", start_cell="0")
assert o7["ok"] and o7["root_mode"] == "explicit", o7
assert o7["start_cell"] == "0" and o7["root_cell_index"] == 0, o7

# ⑧ start_cell 非法条码 → INVALID_INPUT（显式参数严格不兜底）
bad8 = run_pt(engine="palantir", start_cell="NO_SUCH_CELL")
assert not bad8["ok"] and bad8["error_code"] == "INVALID_INPUT", bad8

# ⑨ engine 非法值 → INVALID_INPUT
bad9 = run_pt(engine="monocle")
assert not bad9["ok"] and bad9["error_code"] == "INVALID_INPUT", bad9

# ⑩ 分支推断（BEAM-lite）：独立双分支合成库——trunk t∈[0,0.5]
# 后随机分命运 A/B 延伸 t→1；替换式注入 amp=60 同 Phase 53 纪律
DS2 = "scpseudotimebrsmoke"
n_tr, n_f = 100, 100
t2 = np.concatenate([np.linspace(0, 0.5, n_tr),
                     np.linspace(0.5, 1, n_f),
                     np.linspace(0.5, 1, n_f)])
n2 = len(t2)
fate = np.array(["trunk"] * n_tr + ["A"] * n_f + ["B"] * n_f)
X2 = rng.poisson(2, (n2, 50)).astype(np.float32)
X2[:, 0] = rng.poisson(t2 * 60 + 0.1, n2).astype(np.float32)
sig_a = np.where(fate == "A", (t2 - 0.5) * 2, 0.0)
sig_b = np.where(fate == "B", (t2 - 0.5) * 2, 0.0)
X2[:, 1] = rng.poisson(sig_a * 60 + 0.1, n2).astype(np.float32)
X2[:, 2] = rng.poisson(sig_b * 60 + 0.1, n2).astype(np.float32)
ad2 = sc.AnnData(csr_matrix(X2))
ad2.var_names = ["G_trunk_up", "G_fateA", "G_fateB"] + \
    [f"B{i}" for i in range(3, 50)]
sc.pp.normalize_total(ad2)
sc.pp.log1p(ad2)
ad2.raw = ad2
ad2.var["highly_variable"] = True  # 全 50 基因入候选池（确定性）
sc.pp.pca(ad2)
sc.pp.neighbors(ad2)
ad2.obs["leiden"] = pd.Categorical(fate)  # trunk/A/B 人工分簇
ad2.obsm["X_umap"] = ad2.obsm["X_pca"][:, :2]
ds2_dir = WS / DS2
ds2_dir.mkdir(parents=True, exist_ok=True)
ad2.write_h5ad(ds2_dir / "processed.h5ad")

o10 = run_pt(DS2, engine="palantir", dyn_top_n=0, branch_top_n=50)
assert o10["ok"] and o10["method"] == "palantir", o10
assert o10["n_terminal"] >= 2, o10
assign_rate = 1 - o10["n_unassigned"] / n2
assert assign_rate > 0.5, f"归属率 {assign_rate:.2f} <= 0.5"
assert sum(o10["branch_counts"].values()) + o10["n_unassigned"] == n2

br_dir = ds2_dir / "pseudotime"
de = pd.read_csv(br_dir / "palantir_branch_de.csv")
assign = pd.read_csv(br_dir / "palantir_branch_assign.csv",
                     index_col=0)
assign["branch"] = assign["branch"].astype(str)
top_de = (de[de["qval"] < 0.05]
          .sort_values("score", ascending=False).head(20))
de_genes = set(top_de["gene"])
assert {"G_fateA", "G_fateB"} <= de_genes, (
    f"分支 DE top 缺命运基因: {sorted(de_genes)[:10]}")
for g, lab in (("G_fateA", "A"), ("G_fateB", "B")):
    h = str(top_de.loc[top_de["gene"] == g, "higher_in"].iloc[0])
    frac = float((assign.loc[assign["branch"] == h, "leiden"]
                  == lab).mean())
    assert frac > 0.7, f"{g} higher_in 分支 {lab} 纯度仅 {frac:.2f}"
for f in ("palantir_branch_assign.csv", "palantir_branch_dyn.csv",
          "palantir_branch_de.csv", "palantir_branch_trend.png"):
    assert (br_dir / f).exists(), f
# 分支归属写回 obs（st_trajectory 写回惯例）：重读 processed 验证
ad2_back = sc.read_h5ad(ds2_dir / "processed.h5ad")
assert "palantir_branch" in ad2_back.obs, ad2_back.obs.columns
assert set(ad2_back.obs["palantir_branch"].cat.categories
           ) >= {"unassigned"}, ad2_back.obs["palantir_branch"]

# ⑪ dpt + branch_top_n>0 → INVALID_INPUT（dpt 不推断分支）
bad11 = run_pt(DS2, branch_top_n=50)
assert not bad11["ok"] and bad11["error_code"] == "INVALID_INPUT", bad11

# ⑫ 趋势聚类：palantir dyn50+modules_k=3 → 模块产物+全归属+恰 3 模块
o12 = run_pt(DS2, engine="palantir", dyn_top_n=50, dyn_modules_k=3)
assert o12["ok"] and o12["n_modules"] == 3, o12.keys()
mod_df = pd.read_csv(br_dir / "palantir_dyn_modules.csv")
assert set(mod_df["module"].unique()) == {"M1", "M2", "M3"}
assert len(mod_df) == o12["n_dyn"]
assert sum(o12["module_sizes"].values()) == len(mod_df)
assert (br_dir / "palantir_dyn_modules.png").exists()

# ⑬ 校验：非法 enrich key / modules_k>0 但 dyn_top_n=0 → INVALID_INPUT
bad13 = run_pt(DS2, engine="palantir", dyn_top_n=50,
               dyn_modules_k=3, modules_enrich="nope")
assert not bad13["ok"] and bad13["error_code"] == "INVALID_INPUT", bad13
bad14 = run_pt(DS2, engine="palantir", dyn_top_n=0, dyn_modules_k=3)
assert not bad14["ok"] and bad14["error_code"] == "INVALID_INPUT", bad14

# ⑭ slingshot 引擎：DS2 双分叉库（其主场）root_cluster=trunk+dyn20
# → n_lineages≥2 + 主 pt vs 真值 t2 rho≥0.8 + 三产物 + obs 写回
o14 = run_pt(DS2, engine="slingshot", root_cluster="trunk", dyn_top_n=20)
assert o14["ok"] and o14["method"] == "slingshot", o14
assert o14["root_mode"] == "cluster" and o14["n_lineages"] >= 2, o14
sl_pt = pd.read_csv(br_dir / "slingshot_pt.csv",
                    index_col=0)["slingshot_pseudotime"]
sl_pt.index = sl_pt.index.astype(str)
sl_pt = sl_pt.loc[[str(i) for i in range(n2)]]
mask = sl_pt.notna().to_numpy()
rho_sl = float(spearmanr(sl_pt.to_numpy()[mask], t2[mask]).statistic)
assert rho_sl >= 0.8, f"slingshot pt vs t2 rho={rho_sl}"
for f in ("slingshot_pt.csv", "slingshot_curves.csv",
          "slingshot_umap.png", "slingshot_dyn_genes.csv"):
    assert (br_dir / f).exists(), f
ad2_sl = sc.read_h5ad(ds2_dir / "processed.h5ad")
assert "slingshot_pseudotime" in ad2_sl.obs, ad2_sl.obs.columns
# ⑭b 交叉自动触发（场景⑩已写 palantir_branch）：双产物+主导映射+
# Fisher 三列+n_cross_sig≥1
assert o14["cross_triggered_by"] == "slingshot", o14
assert len(o14["lineage_branch_map"]) == o14["n_lineages"] >= 2, o14
assert o14["n_cross_sig"] >= 1, o14
cross_df = pd.read_csv(br_dir / "slingshot_branch_cross.csv")
for c in ("fisher_p", "fisher_q", "roe", "dominant_branch"):
    assert c in cross_df.columns, cross_df.columns
assert (br_dir / "slingshot_branch_cross.png").exists()

# ⑮ slingshot + branch_top_n>0 → INVALID_INPUT（分支推断 palantir 专属）
bad15 = run_pt(DS2, engine="slingshot", branch_top_n=50)
assert not bad15["ok"] and bad15["error_code"] == "INVALID_INPUT", bad15

# ⑰ PAGA 分析相：dpt+paga → n_paga_edges≥1 + csv/png 双产物
o17 = run_pt(DS2, root_marker="G_trunk_up", paga=True)
assert o17["ok"] and o17["n_paga_edges"] >= 1, o17
for f in ("paga_graph.csv", "paga_umap.png"):
    assert (br_dir / f).exists(), f
pg = pd.read_csv(br_dir / "paga_graph.csv")
assert {"cluster_a", "cluster_b", "weight"} <= set(pg.columns), pg.columns

# ⑱ 反向触发+组合：palantir+branch50+paga+paga_pt → 分支四产物+
# 交叉（triggered_by=palantir）+paga 双产物+obs paga_dpt 列齐备；
# paga_pt=1&paga=0 → INVALID_INPUT
o18 = run_pt(DS2, engine="palantir", branch_top_n=50,
             paga=True, paga_pt=True)
assert o18["ok"] and o18["cross_triggered_by"] == "palantir", o18
assert o18["n_paga_edges"] >= 1 and o18["paga_root_cluster"], o18
assert o18["n_cross_sig"] >= 1, o18
ad2_p = sc.read_h5ad(ds2_dir / "processed.h5ad")
assert "paga_dpt_pseudotime" in ad2_p.obs, ad2_p.obs.columns
bad18 = run_pt(DS2, paga_pt=True)
assert not bad18["ok"] and bad18["error_code"] == "INVALID_INPUT", bad18

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
      "| mutex rejected | dyn_top_n=0 compat",
      f"| palantir rho={rho_pal:.3f} n_terminal={o5['n_terminal']}",
      "| cluster/explicit root ok | bad start_cell/engine rejected",
      f"| branch assign_rate={assign_rate:.2f} "
      f"n_terminal={o10['n_terminal']} de_top={sorted(de_genes)[:4]}",
      "| dpt+branch_top_n rejected",
      f"| modules k={o12['n_modules']} sizes={o12['module_sizes']}",
      "| bad enrich/dyn0 rejected",
      f"| slingshot n_lineages={o14['n_lineages']} rho={rho_sl:.3f}",
      "| slingshot+branch_top_n rejected",
      f"| cross sig={o14['n_cross_sig']}/{o14['n_lineages']}",
      f"| paga edges={o17['n_paga_edges']}",
      f"| palantir+paga_pt cross={o18['cross_triggered_by']}",
      "| paga_pt w/o paga rejected")
