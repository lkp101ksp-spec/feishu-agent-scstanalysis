"""sc_pseudotime：拟时序四引擎（Phase 32/53 DPT + Palantir + Slingshot
+ Phase 62 Monocle3）。

stdin: {"dataset_id": ..., "root_marker": "NKG7",
        "root_cluster": "",   # Phase 53：leiden 簇定根，与 root_marker 互斥
        "dyn_top_n": 50,      # Phase 53：动态基因 top N；0=跳过
        "engine": "dpt",      # dpt（默认）/ palantir / slingshot / monocle3
        "start_cell": "",     # palantir/slingshot/monocle3 显式根条码
        "branch_top_n": 0,    # palantir 专用：分支推断 top N；0=跳过
        "graph_top_n": 0,     # monocle3 专用：graph_test top N；0=跳过
        "dyn_modules_k": 0,   # 动态基因趋势聚类模块数；0=跳过
        "modules_enrich": ""} # 模块富集 GS key（enrichment GS_KEYS）；空=跳过
需 processed.h5ad（含 neighbors 图）。
engine="dpt"：scanpy diffmap + DPT（Haghverdi 2016 图扩散族，覆盖
Monocle 拟时序的排序场景；分支推断/BEAM/CytoTRACE2 不在范围）。
engine="palantir"：马尔可夫链扩散（Setty 2019），附终末态 + 分支
概率宽表；产物 palantir_pt.csv（pt+ts_* 分支概率列）/
terminal_states.csv / palantir_umap.png / palantir_branch_umap.png
（分支概率分面图，每终末态一 panel 封顶 6，0-1 固定色阶），
dyn 产物加 palantir_ 前缀；branch_top_n>0 附分支推断产物
palantir_branch_assign.csv / palantir_branch_dyn.csv /
palantir_branch_de.csv / palantir_branch_trend.png（BEAM-lite：
归属 + 分支内动态基因 + pt 匹配分支间命运决定基因）。
engine="slingshot"：簇级 MST + 主曲线（Street 2018，分叉轨迹强项），
X_umap+leiden CSV 桥接 Rscript /opt/r_tools/slingshot_bridge.R
（knockout.py/knk.R 先例）；产物 slingshot_pt.csv（主 pt=所属谱系
均值 + lineage1..k 宽表）/ slingshot_curves.csv（曲线折点）/
slingshot_umap.png（主 pt 着色+曲线叠加+根红圈）；主 pt 写回
obs["slingshot_pseudotime"] 统一落盘；dyn 产物加 slingshot_ 前缀；
engine="monocle3"：learn_graph 主图 + order_cells 定向（Cao 2019，
分支树强项；重启评估探针钉注见 测试总结第六十段），X_pca+X_umap
CSV 桥接 monocle3_bridge.R；产物 monocle3_pt.csv（断连分区 NA）/
monocle3_graph.csv（MST 折点）/ monocle3_umap.png；主 pt 写回
obs["monocle3_pseudotime"]；dyn 产物加 monocle3_ 前缀；
fallback 定根不传 start.clus（自由推根），其余三模式传根细胞
所在簇标签。
root 四模式：start_cell 显式条码 > root_cluster 簇内度最高 >
root_marker raw 表达最高 > 皆空取第 0 个细胞（结果中说明）。

容器探针（2026-09-13，bio 镜像断网实测）：
- statsmodels.multipletests 随 scanpy 现成可用（BH 校正）；
- spearmanr 零方差基因返回 rho=NaN（ConstantInputWarning），
  须掩掉不进入排序；pt 非有限（不连通）细胞先掩再算；
- 19149×2000 基因逐基因 Spearman 循环仅 3.4s（timeout 1200 宽裕）；
- 移动平均平滑窗口 max(10, n//50)；
- palantir 1.4.5 与镜像 numpy 2.5.2 零 pin 冲突（mellon 1.7.1 已修
  numpy 2 兼容），300 细胞合成梯度全工作流 rho=0.9917
  （palantir_trial.py 留证）；
- 19149 真机（root_cluster=2+dyn50，16g 容器）palantir 全程 75.6s
  （vs DPT 34s，jax 编译开销在内，timeout 1200 宽裕）；与 DPT 的
  pt Spearman 0.654、dyn top10 重叠 6/10（均 T 身份基因）、根簇
  pt 均值全图谱第 2 小、终末态 2 个落于高 pt 远端簇。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, load_adata, read_args, run


def _rf(x: float) -> float | None:
    """round + 非有限值转 None（防 JSON 输出 NaN/Infinity）。"""
    return round(float(x), 4) if np.isfinite(x) else None


def _neighbors_conn(adata: Any) -> Any:
    """邻居图 connectivities：新版 scanpy 存 obsp（h5ad 往返后 uns
    只剩 params），旧版 uns['neighbors'] 兜底。"""
    obsp = getattr(adata, "obsp", None)
    if obsp is not None and "connectivities" in obsp:
        return obsp["connectivities"]
    return adata.uns["neighbors"]["connectivities"]


def _degree_root(adata: Any, clusters: pd.Series, cluster: str) -> int:
    """簇内定根：邻居图 connectivities 子图度最高细胞（全局索引）。"""
    idx = np.flatnonzero(clusters.to_numpy() == cluster)
    sub = _neighbors_conn(adata)[idx][:, idx]
    deg = np.asarray(sub.sum(axis=1)).ravel()
    return int(idx[int(np.argmax(deg))])


def _raw_expr(adata: Any, gene: str) -> np.ndarray:
    """raw 中取单基因表达向量（稀疏/稠密兼容）。"""
    x = adata.raw[:, gene].X
    if hasattr(x, "todense"):
        return np.asarray(x.todense()).ravel()
    return np.asarray(x).ravel()


def _smooth(y: np.ndarray, w: int) -> np.ndarray:
    """移动平均平滑（窗口 w，same 模式保持长度）。"""
    return np.convolve(y, np.ones(w) / w, mode="same")


def _cluster_stats(clusters: pd.Series, pt: np.ndarray) -> pd.DataFrame:
    """每簇 pt 均值/中位数/细胞数表（簇名自然序）。"""
    stats = (pd.DataFrame({"cluster": clusters.values, "pt": pt})
             .groupby("cluster")["pt"]
             .agg(["mean", "median", "size"]))
    return stats.loc[sorted(stats.index, key=lambda c: (len(c), c))]


def _dyn_stats(Xc: Any, col_of: dict[str, int], hvgs: list[str],
               pt: np.ndarray, m: np.ndarray) -> pd.DataFrame:
    """HVG ∩ raw 候选池逐基因 Spearman(pt, raw 表达) + BH 校正。

    供 _dyn_genes（全图谱）与 _branch_analysis（分支内）共用；
    m 为细胞掩码（如 np.isfinite(pt) 或分支归属 ∩ 有限 pt）；
    返回 |rho| 降序 DataFrame(gene, rho, pval, qval)。
    """
    from scipy.stats import spearmanr
    from statsmodels.stats.multitest import multipletests

    pt_m = pt[m]
    rows = []
    for g in hvgs:
        x = np.asarray(Xc[:, col_of[g]].todense()).ravel()[m]
        if x.std() == 0:  # 零方差 → spearmanr 得 NaN，直接排外
            rows.append((g, np.nan, np.nan))
            continue
        r = spearmanr(pt_m, x)
        rows.append((g, r.statistic, r.pvalue))
    df = pd.DataFrame(rows, columns=["gene", "rho", "pval"])
    ok = np.isfinite(df["pval"].to_numpy())
    df["qval"] = np.nan
    df.loc[ok, "qval"] = multipletests(
        df.loc[ok, "pval"], method="fdr_bh")[1]
    return (df.sort_values("rho", key=abs, ascending=False,
                           na_position="last").reset_index(drop=True))


def _dyn_genes(adata: Any, pt: np.ndarray, top_n: int,
               ds_dir: Any, prefix: str = "", modules_k: int = 0,
               modules_enrich: str = "") -> dict[str, Any]:
    """动态基因趋势（Phase 53 "BEAM-lite"）。

    HVG ∩ raw 候选池逐基因 Spearman(pt, raw 表达) + BH 校正
    （零方差/NaN 掩掉，统计部分复用 _dyn_stats）→ |rho| 降序取
    qval<0.05 的 top N；产物 <prefix>dyn_genes.csv（全量）+
    <prefix>trend_heatmap.png（pt 排序×平滑 z-score）+
    <prefix>trend_curves.png（top6 散点+平滑曲线）；prefix 用于
    palantir 引擎产物与 DPT 区分。
    """
    import matplotlib.pyplot as plt

    # OOM 教训（19149 真机验收 probe_pt_rss.py 实证）：逐基因
    # adata.raw[:, g] AnnData 切片每基因漏 ~13MB（500 基因 8.5GB 被杀）；
    # 必须整矩阵一次取 csc + 列索引字典，循环内只做稀疏列切
    raw_names = adata.raw.var_names
    col_of = {g: j for j, g in enumerate(raw_names)}
    Xc = adata.raw.X.tocsc()

    def _col(gene: str) -> np.ndarray:
        return np.asarray(Xc[:, col_of[gene]].todense()).ravel()

    hvgs = [g for g in adata.var_names[adata.var["highly_variable"]]
            if g in col_of]
    m = np.isfinite(pt)
    pt_m, order = pt[m], np.argsort(pt[m])
    df = _dyn_stats(Xc, col_of, hvgs, pt, m)
    sig = df[df["qval"] < 0.05]
    top = sig.head(top_n)
    fallback_note = ""
    if len(top) < 6:  # 显著太少 → 按 |rho| 兜底保证图可读
        top = df.head(top_n)
        fallback_note = (f"sig(q<0.05) only {int(len(sig))}; "
                         "heatmap/curves fall back to top |rho|")
    dyn_csv = ds_dir / f"{prefix}dyn_genes.csv"
    df.to_csv(dyn_csv, index=False)

    # 趋势热图：细胞按 pt 升序 × top 基因（平滑后 z-score）
    genes = top["gene"].tolist()
    w = max(10, int(m.sum()) // 50)
    mat = np.empty((len(genes), int(m.sum())), dtype=float)
    for i, g in enumerate(genes):
        s = _smooth(_col(g)[m][order], w)
        sd = s.std()
        mat[i] = (s - s.mean()) / sd if sd > 0 else 0.0
    fig = plt.figure(figsize=(6.5, max(3, 0.14 * len(genes) + 1.6)))
    gs = fig.add_gridspec(2, 1, height_ratios=[1, max(4, len(genes) // 3)],
                          hspace=0.05)
    ax0 = fig.add_subplot(gs[0])
    ax0.imshow(pt_m[order][None, :], aspect="auto", cmap="viridis")
    ax0.set_xticks([])
    ax0.set_yticks([])
    ax0.set_ylabel("pt", fontsize=7, rotation=0, va="center")
    ax = fig.add_subplot(gs[1])
    im = ax.imshow(mat, aspect="auto", cmap="RdBu_r",
                   vmin=-2, vmax=2)
    ax.set_yticks(range(len(genes)), genes, fontsize=6)
    ax.set_xticks([])
    ax.set_xlabel("cells ordered by pseudotime →", fontsize=8)
    ax.set_title(f"dynamic genes along pseudotime (top {len(genes)})",
                 fontsize=9)
    fig.colorbar(im, ax=ax, shrink=0.6, label="z-score (smoothed)")
    heat_png = ds_dir / f"{prefix}trend_heatmap.png"
    fig.savefig(heat_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # top6 曲线：散点（下采样 500）+ 平滑线
    show = genes[:6]
    fig, axes = plt.subplots(2, 3, figsize=(9, 4.6), sharex=True)
    rng = np.random.default_rng(0)
    sub_idx = (rng.choice(int(m.sum()), size=min(500, int(m.sum())),
                          replace=False))
    for ax, g in zip(axes.ravel(), show):
        x_full = _col(g)[m]
        ax.scatter(pt_m[sub_idx], x_full[sub_idx], s=3, c="#bdbdbd",
                   linewidths=0)
        ax.plot(pt_m[order], _smooth(x_full[order], w), color="#d62728",
                lw=1.6)
        rho = float(top.loc[top["gene"] == g, "rho"].iloc[0])
        ax.set_title(f"{g} (rho={rho:.2f})", fontsize=8)
        ax.tick_params(labelsize=6)
    for ax in axes.ravel()[len(show):]:
        ax.axis("off")
    fig.suptitle("top dynamic genes (smoothed trend)", fontsize=10)
    fig.tight_layout()
    curves_png = ds_dir / f"{prefix}trend_curves.png"
    fig.savefig(curves_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    out: dict[str, Any] = {
        "n_dyn": int(len(sig)),
        "top_dyn": [{"gene": str(r["gene"]), "rho": _rf(r["rho"]),
                     "qval": float(f"{r['qval']:.2e}")}
                    for _, r in top.head(10).iterrows()],
        "dyn_csv": str(dyn_csv),
        "trend_heatmap_png": str(heat_png),
        "trend_curves_png": str(curves_png)}
    if fallback_note:
        out["dyn_note"] = fallback_note
    if modules_k > 0:
        if len(sig) >= modules_k:
            out.update(_dyn_modules(sig, _col, m, order, pt_m, w,
                                    modules_k, modules_enrich,
                                    ds_dir, prefix))
        else:
            out["modules_note"] = (
                f"sig(q<0.05) {int(len(sig))} < k={modules_k}; "
                "modules skipped")
    return out


def _dyn_modules(sig: pd.DataFrame, col_fn: Any, m: np.ndarray,
                 order: np.ndarray, pt_m: np.ndarray, w: int,
                 k: int, enrich_key: str, ds_dir: Any,
                 prefix: str = "") -> dict[str, Any]:
    """动态基因趋势聚类：显著基因全量 → 早→晚表达程序模块。

    sig(q<0.05) 基因沿 pt 平滑 z-score 矩阵 → KMeans(k, seed=0)
    → 模块按 "z>1 高表达细胞加权平均 pt" 排序重命名 M1..Mk；
    产物 <prefix>dyn_modules.csv（gene,rho,qval,module）+
    <prefix>dyn_modules.png（k 条模块均值趋势曲线）；enrich_key
    非空时逐模块 gp.enrich 离线 ORA（enrichment.py GS_KEYS/
    _load_lib 先例，鼠源库符号 .upper() 对齐）。
    """
    import matplotlib.pyplot as plt
    from sklearn.cluster import KMeans

    genes = sig["gene"].tolist()
    mat = np.empty((len(genes), int(m.sum())), dtype=float)
    for i, g in enumerate(genes):
        s = _smooth(col_fn(g)[m][order], w)
        sd = s.std()
        mat[i] = (s - s.mean()) / sd if sd > 0 else 0.0
    lab = KMeans(n_clusters=k, n_init=10,
                 random_state=0).fit_predict(mat)
    # 模块早晚排序：z>1 超出部分作权重，加权平均 pt 越早模块号越小
    pts = pt_m[order]
    peak_pt = []
    for c in range(k):
        sub = mat[lab == c]
        wgt = np.clip(sub - 1.0, 0.0, None)
        tot = float(wgt.sum())
        peak_pt.append(float((wgt * pts[None, :]).sum() / tot)
                       if tot > 0 else float("inf"))
    order_c = np.argsort(peak_pt)
    name_of = {c: f"M{r + 1}" for r, c in enumerate(order_c)}
    mod_df = sig[["gene", "rho", "qval"]].copy()
    mod_df["module"] = [name_of[c] for c in lab]
    mod_csv = ds_dir / f"{prefix}dyn_modules.csv"
    mod_df.to_csv(mod_csv, index=False)

    # 模块均值趋势曲线（单面板 k 条线，M 号=早晚序）
    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    cmap = plt.get_cmap("tab10")
    for r, c in enumerate(order_c):
        sub = mat[lab == c]
        ax.plot(pts, sub.mean(axis=0), color=cmap(r % 10), lw=1.6,
                label=f"M{r + 1} (n={sub.shape[0]})")
    ax.axhline(0, color="#bdbdbd", lw=0.6)
    ax.set_xlabel("pseudotime", fontsize=9)
    ax.set_ylabel("module mean z-score", fontsize=9)
    ax.set_title(f"dynamic gene modules along pseudotime (k={k})",
                 fontsize=10)
    ax.legend(fontsize=8)
    fig.tight_layout()
    mod_png = ds_dir / f"{prefix}dyn_modules.png"
    fig.savefig(mod_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    out: dict[str, Any] = {
        "n_modules": k,
        "module_sizes": {f"M{r + 1}": int((lab == c).sum())
                         for r, c in enumerate(order_c)},
        "dyn_modules_csv": str(mod_csv),
        "dyn_modules_png": str(mod_png)}
    notes: list[str] = []

    if enrich_key:
        import gseapy as gp
        from enrichment import _load_lib
        lib = _load_lib(enrich_key)
        is_mouse = enrich_key.endswith("_mouse")
        rows = []
        for r, c in enumerate(order_c):
            mn = f"M{r + 1}"
            gs = mod_df.loc[mod_df["module"] == mn, "gene"].tolist()
            if len(gs) < 5:
                notes.append(f"{mn} < 5 genes; enrich skipped")
                continue
            ora_in = [g.upper() for g in gs] if is_mouse else gs
            er = gp.enrich(gene_list=ora_in, gene_sets=lib,
                           outdir=None, verbose=False).results
            edf = pd.DataFrame(er)
            if edf.empty:
                continue
            edf = edf.sort_values("Adjusted P-value").head(10)
            for _, e in edf.iterrows():
                rows.append((mn, e["Term"], str(e["Overlap"]),
                             e["P-value"], e["Adjusted P-value"]))
        en_csv = ds_dir / f"{prefix}dyn_modules_enrich.csv"
        pd.DataFrame(rows, columns=["module", "term", "overlap",
                                    "pval", "qval"]).to_csv(
            en_csv, index=False)
        out["dyn_modules_enrich_csv"] = str(en_csv)
        if not rows:
            notes.append("enrich returned no terms for any module")
    if notes:
        out["modules_note"] = "; ".join(notes)
    return out


def _branch_analysis(adata: Any, pt: np.ndarray, pr: Any,
                     terms: list[str], clusters: pd.Series,
                     top_n: int, ds_dir: Any) -> dict[str, Any]:
    """分支推断（BEAM-lite，对齐 Monocle2 BEAM 场景）。

    三步：①argmax(branch_probs) 归属（max_prob<0.6 → unassigned，
    低置信过渡态不强行站队）；②分支内（归属 ≥30 细胞）沿 pt 复用
    _dyn_stats 取 top N；③两两分支 pt 排序 20 等量桶、同序号桶
    中位数差 score=median(|Δ|)、配对 wilcoxon(n=20)+BH → 命运
    决定基因。产物 palantir_branch_assign.csv /
    palantir_branch_dyn.csv / palantir_branch_de.csv /
    palantir_branch_trend.png（显著基因最多那对、top≤20 双面板
    共享 ±2 色阶）。
    """
    import itertools

    import matplotlib.pyplot as plt
    from scipy.stats import wilcoxon
    from statsmodels.stats.multitest import multipletests

    THR = 0.6      # 归属置信阈值
    MIN_CELLS = 30  # 分支参与 dyn/DE 的最少归属细胞数
    N_BIN = 20     # pt 匹配等量桶数（= wilcoxon 样本量）

    # 步骤 1：分支归属
    prob_mat = np.asarray(pr.branch_probs[terms], dtype=float)
    amax = prob_mat.argmax(axis=1)
    maxp = prob_mat.max(axis=1)
    branch = np.array([terms[i] for i in amax], dtype=object)
    branch[maxp < THR] = "unassigned"
    assign_df = pd.DataFrame({"leiden": clusters.values, "pt": pt,
                              "branch": branch, "max_prob": maxp},
                             index=adata.obs_names)
    assign_csv = ds_dir / "palantir_branch_assign.csv"
    assign_df.to_csv(assign_csv)
    # 分支归属写回 obs（st_trajectory dpt 写回惯例），
    # 供 sc_plot 等下游按分支着色；由 main 统一落盘 processed.h5ad
    adata.obs["palantir_branch"] = pd.Categorical(
        branch, categories=terms + ["unassigned"])
    counts = {t: int((branch == t).sum()) for t in terms}
    out: dict[str, Any] = {
        "branch_assign_csv": str(assign_csv),
        "branch_counts": counts,
        "n_unassigned": int((branch == "unassigned").sum())}
    notes: list[str] = []
    if len(terms) < 2:
        out["branch_note"] = (f"n_terminal={len(terms)} < 2; "
                              "assignment only, dyn/DE skipped")
        return out

    # 内存纪律（同 _dyn_genes）：整矩阵一次 csc + 列索引字典
    raw_names = adata.raw.var_names
    col_of = {g: j for j, g in enumerate(raw_names)}
    Xc = adata.raw.X.tocsc()

    def _col(gene: str) -> np.ndarray:
        return np.asarray(Xc[:, col_of[gene]].todense()).ravel()

    hvgs = [g for g in adata.var_names[adata.var["highly_variable"]]
            if g in col_of]
    valid = [t for t in terms if counts[t] >= MIN_CELLS]
    skipped = [t for t in terms if counts[t] < MIN_CELLS]
    if skipped:
        notes.append(f"branches < {MIN_CELLS} cells skipped: "
                     f"{[t[-8:] for t in skipped]}")

    # 步骤 2：分支内动态基因
    dyn_rows = []
    for t in valid:
        m = (branch == t) & np.isfinite(pt)
        df = _dyn_stats(Xc, col_of, hvgs, pt, m)
        sig = df[df["qval"] < 0.05]
        top = sig.head(top_n)
        if len(top) < 6:  # 显著太少 → 按 |rho| 兜底
            top = df.head(top_n)
        for _, r in top.iterrows():
            dyn_rows.append((t, r["gene"], r["rho"], r["qval"]))
    dyn_csv = ds_dir / "palantir_branch_dyn.csv"
    pd.DataFrame(dyn_rows,
                 columns=["branch", "gene", "rho", "qval"]
                 ).to_csv(dyn_csv, index=False)
    out["branch_dyn_csv"] = str(dyn_csv)

    # 步骤 3：两两分支 pt 匹配差异（命运决定基因）
    de_rows = []
    n_pairs = 0
    for a, b in itertools.combinations(valid, 2):
        sa = (branch == a) & np.isfinite(pt)
        sb = (branch == b) & np.isfinite(pt)
        if sa.sum() < MIN_CELLS or sb.sum() < MIN_CELLS:
            continue
        n_pairs += 1
        ia = np.flatnonzero(sa)[np.argsort(pt[sa])]
        ib = np.flatnonzero(sb)[np.argsort(pt[sb])]
        bins_a = np.array_split(ia, N_BIN)
        bins_b = np.array_split(ib, N_BIN)
        pair = f"{a}_vs_{b}"
        for g in hvgs:
            x = _col(g)
            ma = np.array([np.median(x[idx]) for idx in bins_a])
            mb = np.array([np.median(x[idx]) for idx in bins_b])
            d = ma - mb
            if np.all(d == 0):  # 全零差 → wilcoxon 无解
                continue
            p = wilcoxon(d).pvalue
            de_rows.append((pair, g, float(np.median(np.abs(d))),
                            a if np.median(d) > 0 else b, p))
    if n_pairs == 0:
        notes.append("all branch pairs skipped (< "
                     f"{MIN_CELLS} cells); assignment/dyn only")
    if de_rows:
        de_df = pd.DataFrame(de_rows,
                             columns=["pair", "gene", "score",
                                      "higher_in", "pval"])
        de_df["qval"] = np.nan
        for pair, sub in de_df.groupby("pair"):
            de_df.loc[sub.index, "qval"] = multipletests(
                sub["pval"], method="fdr_bh")[1]
        de_df = de_df.sort_values(["pair", "score"],
                                  ascending=[True, False])
        de_csv = ds_dir / "palantir_branch_de.csv"
        de_df.to_csv(de_csv, index=False)
        out["branch_de_csv"] = str(de_csv)

        # 对照热图：显著基因最多那对（top≤20，双面板共享 ±2）
        sig_df = de_df[de_df["qval"] < 0.05]
        if not sig_df.empty:
            best = sig_df["pair"].value_counts().index[0]
            genes = (sig_df[sig_df["pair"] == best]
                     .head(min(20, top_n))["gene"].tolist())
            ta, tb = best.split("_vs_")
            panels = []
            for t in (ta, tb):
                sel = (branch == t) & np.isfinite(pt)
                idx = np.flatnonzero(sel)[np.argsort(pt[sel])]
                panels.append((t, idx))
            w = max(10, min(len(p[1]) for p in panels) // 50)
            fig = plt.figure(figsize=(
                11, max(3, 0.16 * len(genes) + 1.8)))
            gs = fig.add_gridspec(
                2, 2, height_ratios=[1, max(4, len(genes) // 3)],
                hspace=0.05, wspace=0.28)
            im: Any = None  # 循环两次必赋值；Any 平 mypy arg-type
            for j, (t, idx) in enumerate(panels):
                mat = np.empty((len(genes), len(idx)))
                for i, g in enumerate(genes):
                    s = _smooth(_col(g)[idx], w)
                    sd = s.std()
                    mat[i] = (s - s.mean()) / sd if sd > 0 else 0.0
                ax0 = fig.add_subplot(gs[0, j])
                ax0.imshow(pt[idx][None, :], aspect="auto",
                           cmap="viridis")
                ax0.set_xticks([])
                ax0.set_yticks([])
                ax0.set_ylabel("pt", fontsize=7, rotation=0,
                               va="center")
                ax = fig.add_subplot(gs[1, j])
                im = ax.imshow(mat, aspect="auto", cmap="RdBu_r",
                               vmin=-2, vmax=2)
                ax.set_yticks(range(len(genes)), genes, fontsize=6)
                ax.set_xticks([])
                ax.set_xlabel("cells ordered by pseudotime →",
                              fontsize=8)
                ax.set_title(f"branch {t[-8:]} (n={len(idx)})",
                             fontsize=9)
            fig.colorbar(im, ax=fig.axes, shrink=0.5,
                         label="z-score (smoothed)")
            trend_png = ds_dir / "palantir_branch_trend.png"
            fig.savefig(trend_png, dpi=150, bbox_inches="tight")
            plt.close(fig)
            out["branch_trend_png"] = str(trend_png)
            if n_pairs > 1:
                notes.append(
                    f"{n_pairs} pairs; trend heatmap shows most-"
                    f"significant pair only, rest in branch_de csv")
    if notes:
        out["branch_note"] = "; ".join(notes)
    return out


def _run_palantir(adata: Any, iroot: int, clusters: pd.Series,
                  dyn_top_n: int, branch_top_n: int, ds_dir: Any,
                  dyn_modules_k: int = 0,
                  modules_enrich: str = ""
                  ) -> tuple[dict[str, Any], np.ndarray]:
    """Palantir 引擎（Setty 2019）：马尔可夫链扩散伪时序 + 终末态。

    产物：palantir_pt.csv（pt + ts_<barcode> 分支概率宽表列）、
    terminal_states.csv（终末态条码/簇/pt）、palantir_umap.png
    （pt 着色 + 根红圈 + 终末态黑叉）；dyn_top_n>0 时动态基因复用
    _dyn_genes（产物加 palantir_ 前缀）；branch_top_n>0 时分支
    推断复用 _branch_analysis（BEAM-lite 三步）。
    """
    import contextlib
    import sys

    import matplotlib.pyplot as plt
    import palantir

    # palantir 1.4.5 的 run_palantir 有 unconditional print（"Sampling and
    # flocking waypoints..." 等，n_cells > num_waypoints 时触发）直出 stdout，
    # 会破坏 emit 的"stdout 唯一 JSON"纪律 → 全部进度输出转 stderr。
    start = str(adata.obs_names[iroot])
    with contextlib.redirect_stdout(sys.stderr):
        palantir.utils.run_diffusion_maps(adata, n_components=5)
        palantir.utils.determine_multiscale_space(adata)
        pr = palantir.core.run_palantir(adata, start, num_waypoints=1200)
    pt = np.asarray(pr.pseudotime, dtype=float)
    terms = [str(t) for t in pr.branch_probs.columns]

    # 产物 1：pt + 分支概率宽表（一文件齐）
    pt_df = pd.DataFrame({"leiden": clusters.values,
                          "palantir_pseudotime": pt},
                         index=adata.obs_names)
    for t in terms:
        pt_df[f"ts_{t}"] = np.asarray(pr.branch_probs[t], dtype=float)
    pt_csv = ds_dir / "palantir_pt.csv"
    pt_df.to_csv(pt_csv)

    # 产物 2：终末态表
    tidx = [int(np.flatnonzero(adata.obs_names == t)[0]) for t in terms]
    term_df = pd.DataFrame({
        "cell": terms,
        "leiden": [str(clusters.iloc[j]) for j in tidx],
        "pseudotime": [_rf(pt[j]) for j in tidx]})
    term_csv = ds_dir / "terminal_states.csv"
    term_df.to_csv(term_csv, index=False)

    # 产物 3：UMAP（pt viridis + 根红圈 + 终末态黑叉）
    umap = np.asarray(adata.obsm["X_umap"])
    fig, ax = plt.subplots(figsize=(5.2, 4.2))
    s = ax.scatter(umap[:, 0], umap[:, 1], s=4, c=pt, cmap="viridis",
                   linewidths=0)
    ax.scatter(umap[iroot, 0], umap[iroot, 1], s=90, facecolors="none",
               edgecolors="red", linewidths=1.6, label="root")
    if tidx:
        ax.scatter(umap[tidx, 0], umap[tidx, 1], s=70, marker="x",
                   c="black", linewidths=1.8, label="terminal")
    ax.legend(loc="upper right", fontsize=8)
    fig.colorbar(s, ax=ax, fraction=0.046, label="Palantir pseudotime")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"Palantir pseudotime (root: {start})", fontsize=9)
    fig.tight_layout()
    umap_png = ds_dir / "palantir_umap.png"
    fig.savefig(umap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 产物 4：分支概率 UMAP 分面图（每终末态一 panel，封顶 6；
    # vmin/vmax=0/1 固定色阶保证跨 panel 可比）
    show = terms
    branch_viz_note = ""
    if len(terms) > 6:
        peak = {t: float(np.asarray(pr.branch_probs[t]).max())
                for t in terms}
        show = sorted(terms, key=lambda t: -peak[t])[:6]
        branch_viz_note = (
            f"n_terminal={len(terms)} > 6; "
            "branch umap shows top-6 by max probability")
    k = max(1, len(show))
    fig, axes = plt.subplots(1, k, figsize=(4.6 * k, 4.0),
                             squeeze=False)
    show_idx = {t: int(np.flatnonzero(adata.obs_names == t)[0])
                for t in show}
    for ax, t in zip(axes.ravel(), show):
        prob = np.asarray(pr.branch_probs[t], dtype=float)
        s = ax.scatter(umap[:, 0], umap[:, 1], s=4, c=prob,
                       cmap="viridis", vmin=0, vmax=1, linewidths=0)
        j = show_idx[t]
        ax.scatter(umap[j, 0], umap[j, 1], s=70, marker="x",
                   c="black", linewidths=1.8)
        ax.scatter(umap[iroot, 0], umap[iroot, 1], s=80,
                   facecolors="none", edgecolors="red", linewidths=1.4)
        ax.set_title(f"ts_{t[-8:]} (leiden {clusters.iloc[j]})",
                     fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.colorbar(s, ax=ax, fraction=0.046, label="branch prob")
    fig.suptitle("Palantir branch probabilities", fontsize=10)
    fig.tight_layout()
    branch_png = ds_dir / "palantir_branch_umap.png"
    fig.savefig(branch_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    dyn: dict[str, Any] = {}
    if dyn_top_n > 0:
        dyn = _dyn_genes(adata, pt, dyn_top_n, ds_dir,
                         prefix="palantir_", modules_k=dyn_modules_k,
                         modules_enrich=modules_enrich)

    branch: dict[str, Any] = {}
    if branch_top_n > 0:
        branch = _branch_analysis(adata, pt, pr, terms, clusters,
                                  branch_top_n, ds_dir)

    out = {
        "start_cell": start,
        "n_terminal": len(terms),
        "terminal_states": term_df.head(5).to_dict("records"),
        **dyn,
        **branch,
        "pseudotime_csv": str(pt_csv),
        "umap_png": str(umap_png),
        "terminal_csv": str(term_csv),
        "branch_umap_png": str(branch_png),
    }
    if branch_viz_note:
        out["branch_viz_note"] = branch_viz_note
    return out, pt


def _run_slingshot(adata: Any, iroot: int, clusters: pd.Series,
                   root_mode: str, dyn_top_n: int, ds_dir: Any,
                   dyn_modules_k: int = 0,
                   modules_enrich: str = "") -> tuple[dict[str, Any], Any]:
    """Slingshot 引擎（spec 2026-09-13-sc-slingshot-engine-design.md）。

    X_umap + leiden → CSV 桥接 → Rscript slingshot_bridge.R（簇级 MST
    getLineages + 主曲线 getCurves）→ 读回 sling_pst 宽表，主 pt =
    细胞所属谱系 pt 行均值（NA 忽略）；写 obs["slingshot_pseudotime"]
    （main 统一落盘）；产物 slingshot_pt.csv / slingshot_curves.csv /
    slingshot_umap.png（主 pt 着色 + 谱系曲线 tab10 叠加 + 根红圈）；
    dyn 相复用 _dyn_genes（prefix="slingshot_"）。out 另带谱系路径
    结构化字段（lineage_starts/lineage_paths/anchor_ok/anchor_note，
    R 侧 metadata$lineages 直读——锚定 sanity 权威口径）。
    """
    import subprocess

    import matplotlib.pyplot as plt

    in_dir = ds_dir / "_sling_in"
    in_dir.mkdir(parents=True, exist_ok=True)
    umap = np.asarray(adata.obsm["X_umap"])
    reduced = pd.DataFrame({"UMAP1": umap[:, 0], "UMAP2": umap[:, 1]},
                           index=adata.obs_names)
    reduced.index.name = "cell"
    reduced_csv = in_dir / "reduced.csv"
    reduced.to_csv(reduced_csv)
    cl_df = pd.DataFrame({"leiden": clusters.values},
                         index=adata.obs_names)
    cl_df.index.name = "cell"
    clusters_csv = in_dir / "clusters.csv"
    cl_df.to_csv(clusters_csv)

    # fallback 自由推根不传 start.clus；其余三模式传根细胞所在簇
    start_clus = "" if root_mode == "fallback" \
        else str(clusters.iloc[iroot])
    r_cmd = ["Rscript", "/opt/r_tools/slingshot_bridge.R",
             str(reduced_csv), str(clusters_csv), str(in_dir), start_clus]
    proc = subprocess.run(r_cmd, capture_output=True, text=True,
                          timeout=3300)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Rscript slingshot_bridge.R failed: {proc.stderr[-1500:]}")
    pst_path = in_dir / "sling_pst.csv"
    if not pst_path.exists():
        raise RuntimeError(
            "slingshot_bridge.R did not produce sling_pst.csv; "
            f"stdout tail: {proc.stdout[-500:]}")

    # 数字型条码经 csv 往返被 pandas 解析为 int64，与 obs_names（str）
    # 错位 → reindex 前统一 astype(str)（冒烟场景⑤同款教训）
    pst = pd.read_csv(pst_path, index_col=0)
    pst.index = pst.index.astype(str)
    pst = pst.reindex(adata.obs_names)
    lin_cols = list(pst.columns)
    pt = pst.mean(axis=1, skipna=True).to_numpy(dtype=float)

    pt_df = pd.DataFrame({"leiden": clusters.values,
                          "slingshot_pseudotime": pt},
                         index=adata.obs_names)
    for c in lin_cols:
        pt_df[c] = pst[c].to_numpy(dtype=float)
    pt_csv = ds_dir / "slingshot_pt.csv"
    pt_df.to_csv(pt_csv)
    curves = pd.read_csv(in_dir / "sling_curves.csv")
    curves_csv = ds_dir / "slingshot_curves.csv"
    curves.to_csv(curves_csv, index=False)

    # 谱系路径读回（锚定 sanity 结构化口径，2026-09-17 翻案教训：
    # start.clus 是否生效看 R 侧谱系起点，而非 pst 最小段簇构成——
    # UMAP 重叠带 + 簇大小悬殊下后者必然误判）；bridge 同仓同步
    # 部署必产该文件，缺失即部署漂移，严格报错
    lin_path_p = in_dir / "sling_lineages.csv"
    if not lin_path_p.exists():
        raise RuntimeError(
            "slingshot_bridge.R did not produce sling_lineages.csv; "
            f"stdout tail: {proc.stdout[-500:]}")
    lin_paths = pd.read_csv(lin_path_p)
    lineage_starts = lin_paths["start"].astype(str).tolist()

    adata.obs["slingshot_pseudotime"] = pt
    # 谱系归属物化写回 obs（Phase 57）：argmax 谱系标签，全 NA→unassigned，
    # 供 sc_plot 按谱系着色 / sc_cellfreq 以谱系为 celltype_col 消费
    assign = pd.Series("unassigned", index=adata.obs_names, dtype=object)
    valid_lin = pst.notna().any(axis=1)
    if valid_lin.any():
        assign.loc[valid_lin] = pst.loc[valid_lin].idxmax(axis=1)
    adata.obs["slingshot_lineage"] = pd.Categorical(assign)

    fig, ax = plt.subplots(figsize=(5.6, 4.4))
    s = ax.scatter(umap[:, 0], umap[:, 1], s=4, c=pt, cmap="viridis",
                   linewidths=0)
    palette = plt.get_cmap("tab10")
    for k, (lin, g) in enumerate(curves.groupby("lineage", sort=False)):
        g = g.sort_values("ord")
        ax.plot(g["UMAP1"].to_numpy(), g["UMAP2"].to_numpy(),
                color=palette(k % 10), lw=2.0, alpha=0.9, label=lin,
                zorder=3)
    ax.scatter(umap[iroot, 0], umap[iroot, 1], s=90, facecolors="none",
               edgecolors="red", linewidths=1.6, label="root", zorder=4)
    ax.legend(loc="upper right", fontsize=7)
    fig.colorbar(s, ax=ax, fraction=0.046, label="slingshot pseudotime")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"Slingshot ({len(lin_cols)} lineages)", fontsize=9)
    fig.tight_layout()
    umap_png = ds_dir / "slingshot_umap.png"
    fig.savefig(umap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    out: dict[str, Any] = {
        "n_lineages": len(lin_cols),
        "lineages": lin_cols,
        "lineage_starts": lineage_starts,
        "lineage_paths": {r["lineage"]: r["path"] for _, r in
                          lin_paths.iterrows()},
        # 锚定生效判据：传 start.clus 时全部谱系起点应唯一等于该簇；
        # fallback 自由推根恒 True（起点由 R 侧自定，如实上报）
        "anchor_ok": bool(not start_clus or
                          set(lineage_starts) == {start_clus}),
        "anchor_note": "lineage starts: " + ", ".join(
            sorted(set(lineage_starts))),
        "slingshot_pt_csv": str(pt_csv),
        "slingshot_curves_csv": str(curves_csv),
        "umap_png": str(umap_png),
    }
    if dyn_top_n > 0:
        out.update(_dyn_genes(adata, pt, dyn_top_n, ds_dir,
                              prefix="slingshot_",
                              modules_k=dyn_modules_k,
                              modules_enrich=modules_enrich))
    return out, pt


def _run_monocle3(adata: Any, iroot: int, dyn_top_n: int, ds_dir: Any,
                  dyn_modules_k: int = 0, modules_enrich: str = "",
                  graph_top_n: int = 0) -> tuple[dict[str, Any], Any]:
    """Monocle3 引擎（重启评估落地，测试总结第六十段探针钉注）。

    X_pca + X_umap CSV 桥接 → Rscript monocle3_bridge.R（PCA 当表达
    建 cds → 灌 reducedDims → cluster_cells → learn_graph 主图 →
    order_cells 定向）→ 读回 mono3_pt；写 obs["monocle3_pseudotime"]
    （main 统一落盘）；产物 monocle3_pt.csv / monocle3_graph.csv /
    monocle3_umap.png（pt 着色 + 主图 MST 折线叠加 + 根红圈）；
    dyn 相复用 _dyn_genes（prefix="monocle3_"）。断连分区 pt=NA，
    保 NaN 并在 out 计数（monocle3 多分区语义，不静默填值）。
    graph_top_n>0 时追加真表达 mtx 通道（X 全 HVG cells×genes
    mmwrite + genes.csv）→ bridge graph_test 沿主图基因级 Moran's I
    → 产物 monocle3_graphtest.csv（q_value 升序 top N）+
    out n_graph_sig（q<0.05 计数）。
    """
    import subprocess

    import matplotlib.pyplot as plt

    if "X_pca" not in adata.obsm:
        raise RuntimeError(
            "processed.h5ad lacks X_pca; run sc_process first")
    in_dir = ds_dir / "_mono3_in"
    in_dir.mkdir(parents=True, exist_ok=True)
    n_pc = min(50, adata.obsm["X_pca"].shape[1])
    pca = np.asarray(adata.obsm["X_pca"])[:, :n_pc]
    pca_df = pd.DataFrame(
        pca, index=adata.obs_names,
        columns=[f"PC{i + 1}" for i in range(n_pc)])
    pca_df.index.name = "cell"
    pca_csv = in_dir / "pca.csv"
    pca_df.to_csv(pca_csv)
    umap = np.asarray(adata.obsm["X_umap"])
    ump_df = pd.DataFrame({"UMAP1": umap[:, 0], "UMAP2": umap[:, 1]},
                          index=adata.obs_names)
    ump_df.index.name = "cell"
    umap_csv = in_dir / "umap.csv"
    ump_df.to_csv(umap_csv)

    expr_mtx = ""
    genes_csv_path = ""
    if graph_top_n > 0:
        # 真表达 mtx 通道：X（log1p HVG，cells×genes）稀疏化 mmwrite
        # + 基因清单 csv；bridge 检测到时以真表达重建 cds 表达层
        from scipy.io import mmwrite
        from scipy.sparse import csr_matrix, issparse
        # X 形态双轨：真机 h5ad dense → ndarray；合成冒烟库 csr →
        # 直接沿用（np.asarray(sparse) 会包成 object 数组炸 ValueError）
        x_sp = adata.X if issparse(adata.X) else np.asarray(adata.X)
        x_sp = csr_matrix(x_sp.astype(np.float32))
        expr_mtx_p = in_dir / "expr.mtx"
        mmwrite(str(expr_mtx_p), x_sp)
        genes_p = in_dir / "genes.csv"
        pd.Series(adata.var_names.astype(str)).to_csv(
            genes_p, index=False, header=False)
        expr_mtx = str(expr_mtx_p)
        genes_csv_path = str(genes_p)

    root_cell = str(adata.obs_names[iroot])
    r_cmd = ["Rscript", "/opt/r_tools/monocle3_bridge.R",
             str(pca_csv), str(umap_csv), str(in_dir), root_cell,
             expr_mtx, genes_csv_path]
    proc = subprocess.run(r_cmd, capture_output=True, text=True,
                          timeout=3300)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Rscript monocle3_bridge.R failed: {proc.stderr[-1500:]}")
    pt_path = in_dir / "mono3_pt.csv"
    if not pt_path.exists():
        raise RuntimeError(
            "monocle3_bridge.R did not produce mono3_pt.csv; "
            f"stdout tail: {proc.stdout[-500:]}")

    # 数字型条码 csv 往返 astype(str) 对齐（slingshot 同款教训）
    pt_s = pd.read_csv(pt_path, index_col=0)["pseudotime"]
    pt_s.index = pt_s.index.astype(str)
    pt_s = pt_s.reindex(adata.obs_names)
    pt = pt_s.to_numpy(dtype=float)
    n_na = int(pt_s.isna().sum())

    pt_df = pd.DataFrame({"monocle3_pseudotime": pt},
                         index=adata.obs_names)
    pt_csv = ds_dir / "monocle3_pt.csv"
    pt_df.to_csv(pt_csv)
    graph = pd.read_csv(in_dir / "mono3_graph.csv")
    graph_csv = ds_dir / "monocle3_graph.csv"
    graph.to_csv(graph_csv, index=False)

    adata.obs["monocle3_pseudotime"] = pt

    fig, ax = plt.subplots(figsize=(5.6, 4.4))
    s = ax.scatter(umap[:, 0], umap[:, 1], s=4, c=pt, cmap="viridis",
                   linewidths=0)
    for _, e in graph.iterrows():
        ax.plot([e["x1"], e["x2"]], [e["y1"], e["y2"]],
                color="black", lw=0.9, alpha=0.8, zorder=3)
    ax.scatter(umap[iroot, 0], umap[iroot, 1], s=90, facecolors="none",
               edgecolors="red", linewidths=1.6, label="root", zorder=4)
    ax.legend(loc="upper right", fontsize=7)
    fig.colorbar(s, ax=ax, fraction=0.046, label="monocle3 pseudotime")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"Monocle3 learn_graph ({len(graph)} edges)", fontsize=9)
    fig.tight_layout()
    umap_png = ds_dir / "monocle3_umap.png"
    fig.savefig(umap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    out: dict[str, Any] = {
        "n_graph_edges": int(len(graph)),
        "n_na_pseudotime": n_na,
        "monocle3_pt_csv": str(pt_csv),
        "monocle3_graph_csv": str(graph_csv),
        "umap_png": str(umap_png),
    }
    if graph_top_n > 0:
        gt_path = in_dir / "mono3_graphtest.csv"
        if not gt_path.exists():
            raise RuntimeError(
                "monocle3_bridge.R did not produce mono3_graphtest.csv; "
                f"stdout tail: {proc.stdout[-500:]}")
        gt = pd.read_csv(gt_path)
        gt_csv = ds_dir / "monocle3_graphtest.csv"
        gt.head(graph_top_n).to_csv(gt_csv, index=False)
        n_sig = int((pd.to_numeric(gt["q_value"],
                                   errors="coerce") < 0.05).sum())
        out["monocle3_graphtest_csv"] = str(gt_csv)
        out["n_graph_sig"] = n_sig
        out["graph_top_genes"] = [str(g) for g in
                                  gt["gene"].head(graph_top_n)]
    if dyn_top_n > 0:
        out.update(_dyn_genes(adata, pt, dyn_top_n, ds_dir,
                              prefix="monocle3_",
                              modules_k=dyn_modules_k,
                              modules_enrich=modules_enrich))
    return out, pt


def _load_pst_wide(ds_dir: Any, adata: Any) -> pd.DataFrame | None:
    """读 slingshot_pt.csv 谱系宽表（lineage* 列）对齐 obs_names。

    数字条码 csv 往返被 pandas 解析为 int64（场景⑤/⑭同款教训）→
    astype(str) 再 reindex；无文件或无 lineage 列返回 None。
    """
    p = ds_dir / "slingshot_pt.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, index_col=0)
    df.index = df.index.astype(str)
    lin = [c for c in df.columns if c.startswith("lineage")]
    if not lin:
        return None
    return df[lin].reindex(adata.obs_names)


def _branch_lineage_cross(ds_dir: Any, adata: Any, pst: pd.DataFrame,
                          triggered_by: str) -> dict[str, Any]:
    """谱系×palantir 分支交叉聚合（spec §1，双向触发共享函数）。

    谱系归属 = 每细胞 lineage1..k argmax（全 NA 剔除）；分支 =
    obs["palantir_branch"]（unassigned 剔除）。列联+行比例+主导分支
    （frac≥0.5 否则 mixed）+ 每谱系×主导支 2×2 Fisher + BH + roe
    （cellfreq.py 已验模式）。产物 slingshot_branch_cross.csv
    （一行一谱系全指标）/ .png（行比例热图）。
    """
    import matplotlib.pyplot as plt
    from scipy.stats import fisher_exact
    from statsmodels.stats.multitest import multipletests

    branch = adata.obs["palantir_branch"].astype(str)
    assign = pd.Series("unassigned", index=pst.index, dtype=object)
    valid = pst.notna().any(axis=1)
    if valid.any():
        assign.loc[valid] = pst.loc[valid].idxmax(axis=1)
    keep = (assign != "unassigned") & (branch != "unassigned")
    a, b = assign[keep], branch[keep]
    ct = pd.crosstab(a, b)
    frac = ct.div(ct.sum(axis=1), axis=0)
    branches = list(ct.columns)

    rows: list[dict[str, Any]] = []
    pvals: list[float] = []
    for lin in ct.index:
        top_branch = str(frac.loc[lin].idxmax())
        top_frac = float(frac.loc[lin, top_branch])
        dom = top_branch if top_frac >= 0.5 else "mixed"
        in_lin = (a == lin).to_numpy()
        in_br = (b == top_branch).to_numpy()
        tab = np.array([
            [int((in_lin & in_br).sum()), int((in_lin & ~in_br).sum())],
            [int((~in_lin & in_br).sum()), int((~in_lin & ~in_br).sum())]])
        _, p = fisher_exact(tab)
        pvals.append(float(p))
        exp = float(tab[0].sum() * tab[:, 0].sum() / tab.sum())
        row: dict[str, Any] = {
            "lineage": lin, "n_cells": int(ct.loc[lin].sum()),
            "dominant_branch": dom, "dominant_frac": _rf(top_frac),
            "fisher_branch": top_branch,
            "fisher_p": float(p),
            "roe": _rf(tab[0, 0] / exp) if exp > 0 else float("nan")}
        for br in branches:
            row[f"n_{br}"] = int(ct.loc[lin, br])
            row[f"frac_{br}"] = _rf(float(frac.loc[lin, br]))
        rows.append(row)
    qvals = multipletests(pvals, method="fdr_bh")[1] if pvals else []
    for r, qq in zip(rows, qvals):
        r["fisher_q"] = float(qq)
    cross_csv = ds_dir / "slingshot_branch_cross.csv"
    pd.DataFrame(rows).to_csv(cross_csv, index=False)

    fig, ax = plt.subplots(
        figsize=(max(3.6, 1.2 * len(branches) + 2.2),
                 max(2.4, 0.5 * len(ct.index) + 1.2)))
    im = ax.imshow(frac.to_numpy(), cmap="viridis", vmin=0, vmax=1,
                   aspect="auto")
    ax.set_xticks(range(len(branches)))
    ax.set_xticklabels(branches, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(ct.index)))
    ax.set_yticklabels(ct.index, fontsize=8)
    for i in range(len(ct.index)):
        for j in range(len(branches)):
            v = float(frac.iloc[i, j])
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    fontsize=7, color="white" if v < 0.6 else "black")
    fig.colorbar(im, ax=ax, fraction=0.046, label="row fraction")
    ax.set_title(f"lineage x palantir branch (trigger: {triggered_by})",
                 fontsize=9)
    fig.tight_layout()
    cross_png = ds_dir / "slingshot_branch_cross.png"
    fig.savefig(cross_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 谱系→主导支映射物化写回 obs（Phase 57）：细胞级命运支标签，
    # mixed 谱系标 "mixed"、unassigned 谱系标 "unassigned"，
    # 供 sc_plot 着色 / sc_cellfreq celltype_col="lineage_branch" 消费
    dom_map = {r["lineage"]: r["dominant_branch"] for r in rows}
    adata.obs["lineage_branch"] = pd.Categorical(
        assign.map(lambda x: dom_map.get(x, "unassigned")))

    return {
        "branch_cross_csv": str(cross_csv),
        "branch_cross_png": str(cross_png),
        "lineage_branch_map": {
            r["lineage"]: {"branch": r["dominant_branch"],
                           "frac": r["dominant_frac"]} for r in rows},
        "n_cross_sig": int(sum(1 for r in rows
                               if r.get("fisher_q", 1.0) < 0.05)),
        "cross_triggered_by": triggered_by,
    }


def _run_paga(adata: Any, pt: Any, clusters: pd.Series, ds_dir: Any,
              paga_pt: bool, root_cluster: str) -> dict[str, Any]:
    """PAGA 簇级分析相（spec §2）：tl.paga 连接图 + 可选 PAGA-init DPT。

    产物 paga_graph.csv（cluster_a/cluster_b/weight 全边）/
    paga_umap.png（灰底细胞+簇质心节点按簇 pt 均值 viridis 着色+
    weight≥0.1 边粗细∝weight）。paga_pt=True 时：PAGA 图度=1 端点簇
    中取 pt 均值最小者定根（显式 root_cluster 优先；无端点退化全局
    最小 pt 簇）→ 度中心 iroot → diffmap+DPT →
    obs["paga_dpt_pseudotime"]（main 统一落盘），节点改按 paga_dpt
    簇均值着色。scanpy 无 paga_paths，此为官方 tutorial 配方。
    """
    import matplotlib.pyplot as plt
    import scanpy as sc

    sc.tl.paga(adata, groups="leiden")
    cats = [str(c) for c in adata.obs["leiden"].cat.categories]
    conn = adata.uns["paga"]["connectivities"].toarray()
    edges = [(cats[i], cats[j], float(conn[i, j]))
             for i in range(len(cats)) for j in range(i + 1, len(cats))
             if conn[i, j] > 0]
    graph_csv = ds_dir / "paga_graph.csv"
    pd.DataFrame(edges, columns=["cluster_a", "cluster_b", "weight"]
                 ).to_csv(graph_csv, index=False)
    out: dict[str, Any] = {
        "n_paga_edges": int(sum(1 for e in edges if e[2] >= 0.1)),
        "paga_graph_csv": str(graph_csv),
    }

    node_pt = np.asarray(pt, dtype=float)
    pt_label = "cluster mean pt"
    if paga_pt:
        cl_pt = pd.Series(node_pt, index=adata.obs_names).groupby(
            clusters.to_numpy()).mean()
        deg = (conn > 0).sum(axis=0)
        endpoints = [cats[k] for k in range(len(cats))
                     if deg[k] == 1 and not np.isnan(
                         cl_pt.get(cats[k], np.nan))]
        if root_cluster and root_cluster in cats:
            root_cl = root_cluster
            root_note = f"explicit root_cluster {root_cl}"
        elif endpoints:
            root_cl = min(endpoints,
                          key=lambda c: float(cl_pt.get(c, np.inf)))
            root_note = (f"paga endpoints {endpoints}; "
                         f"min-pt root {root_cl}")
        else:
            root_cl = str(cl_pt.idxmin())
            root_note = (f"no degree-1 endpoint; "
                         f"global min-pt cluster {root_cl}")
        iroot2 = _degree_root(adata, clusters, root_cl)
        adata.uns["iroot"] = iroot2
        sc.tl.diffmap(adata)
        sc.tl.dpt(adata)
        node_pt = adata.obs["dpt_pseudotime"].to_numpy(dtype=float)
        node_pt = np.where(np.isfinite(node_pt), node_pt, np.nan)
        adata.obs["paga_dpt_pseudotime"] = node_pt
        pt_label = "cluster mean paga_dpt"
        out.update({
            "paga_root_cluster": root_cl,
            "paga_root_note": root_note,
            "paga_pt_obs": "paga_dpt_pseudotime",
        })

    umap = np.asarray(adata.obsm["X_umap"])
    cent = pd.DataFrame({"x": umap[:, 0], "y": umap[:, 1],
                         "c": clusters.to_numpy()},
                        index=adata.obs_names).groupby("c").mean()
    cl_node = pd.Series(node_pt, index=adata.obs_names).groupby(
        clusters.to_numpy()).mean()
    fig, ax = plt.subplots(figsize=(5.6, 4.6))
    ax.scatter(umap[:, 0], umap[:, 1], s=3, c="lightgrey", linewidths=0)
    maxw = max((e[2] for e in edges), default=1.0)
    for ca, cb, w in edges:
        if w < 0.1 or ca not in cent.index or cb not in cent.index:
            continue
        ax.plot([cent.loc[ca, "x"], cent.loc[cb, "x"]],
                [cent.loc[ca, "y"], cent.loc[cb, "y"]],
                color="black", lw=3.0 * w / maxw, alpha=0.5, zorder=2)
    s = ax.scatter(cent["x"], cent["y"], s=60,
                   c=[cl_node.get(c, np.nan) for c in cent.index],
                   cmap="viridis", edgecolors="black", linewidths=0.6,
                   zorder=3)
    for c in cent.index:
        ax.annotate(str(c), (cent.loc[c, "x"], cent.loc[c, "y"]),
                    fontsize=6, ha="center", va="center", zorder=4,
                    color="white")
    fig.colorbar(s, ax=ax, fraction=0.046, label=pt_label)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("PAGA graph (edges weight>=0.1)", fontsize=9)
    fig.tight_layout()
    paga_png = ds_dir / "paga_umap.png"
    fig.savefig(paga_png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    out["paga_umap_png"] = str(paga_png)
    return out


def main() -> None:
    """主流程：定根四模式 → 按 engine 分路（DPT / Palantir / Slingshot
    / Monocle3）→ 可选 PAGA 相 / 谱系×分支交叉（双向触发）。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    root_marker = str(args.get("root_marker", "")).strip()
    root_cluster = str(args.get("root_cluster", "")).strip()
    dyn_top_n = int(args.get("dyn_top_n", 50))
    branch_top_n = int(args.get("branch_top_n", 0))
    dyn_modules_k = int(args.get("dyn_modules_k", 0))
    modules_enrich = str(args.get("modules_enrich", "")).strip()
    graph_top_n = int(args.get("graph_top_n", 0))
    paga = bool(args.get("paga", False))
    paga_pt = bool(args.get("paga_pt", False))
    if paga_pt and not paga:
        fail("INVALID_INPUT", "paga_pt=true requires paga=true")
        return
    trajectory_full = bool(args.get("trajectory_full", False))
    engine = str(args.get("engine", "dpt")).strip().lower()
    start_cell = str(args.get("start_cell", "")).strip()
    if engine not in ("dpt", "palantir", "slingshot", "monocle3"):
        fail("INVALID_INPUT",
             f"engine {engine!r} not in "
             "['dpt', 'palantir', 'slingshot', 'monocle3']")
        return
    # trajectory_full（Phase 59）忽略 engine：start_cell/branch_top_n 的
    # 引擎限定随之解除（两相共用 start_cell；branch_top_n 缺省升 50）
    if start_cell and not trajectory_full \
            and engine not in ("palantir", "slingshot", "monocle3"):
        fail("INVALID_INPUT",
             "start_cell only valid with "
             "engine='palantir'/'slingshot'/'monocle3'")
        return
    if branch_top_n > 0 and engine != "palantir" and not trajectory_full:
        fail("INVALID_INPUT",
             "branch_top_n only valid with engine='palantir'")
        return
    if graph_top_n > 0 and engine != "monocle3":
        fail("INVALID_INPUT",
             "graph_top_n only valid with engine='monocle3'")
        return
    if dyn_modules_k > 0 and dyn_top_n <= 0:
        fail("INVALID_INPUT",
             "dyn_modules_k>0 requires dyn_top_n>0")
        return
    if modules_enrich:
        from enrichment import GS_KEYS
        if modules_enrich not in GS_KEYS:
            fail("INVALID_INPUT",
                 f"modules_enrich {modules_enrich!r} not in "
                 f"{sorted(GS_KEYS)}")
            return
    if root_marker and root_cluster:
        fail("INVALID_INPUT",
             "root_marker and root_cluster are mutually exclusive; "
             "provide exactly one")
        return

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    if "leiden" not in adata.obs or "X_umap" not in adata.obsm:
        fail("INVALID_INPUT", "processed.h5ad lacks leiden/X_umap; "
                              "run sc_process first")
        return
    if "neighbors" not in adata.uns:
        fail("INVALID_INPUT", "processed.h5ad lacks neighbors graph; "
                              "run sc_process first")
        return
    clusters = adata.obs["leiden"].astype(str)

    # 定根四模式：start_cell 显式 > cluster 度根 > marker 最高 > cell#0
    root_mode = "fallback"
    if start_cell:
        hits = np.flatnonzero(adata.obs_names == start_cell)
        if len(hits) == 0:
            fail("INVALID_INPUT",
                 f"start_cell {start_cell!r} not in obs_names")
            return
        iroot = int(hits[0])
        root_mode = "explicit"
        root_note = f"explicit start_cell {start_cell} (#{iroot})"
    elif root_cluster:
        avail = sorted(clusters.unique().tolist(),
                       key=lambda c: (len(c), c))
        if root_cluster not in set(clusters):
            fail("INVALID_INPUT",
                 f"root_cluster {root_cluster!r} not in leiden; "
                 f"available: {avail}")
            return
        iroot = _degree_root(adata, clusters, root_cluster)
        root_mode = "cluster"
        root_note = f"cluster {root_cluster} degree-root cell #{iroot}"
    elif root_marker and root_marker in adata.raw.var_names:
        expr = _raw_expr(adata, root_marker)
        iroot = int(np.argmax(expr))
        root_mode = "marker"
        root_note = f"{root_marker}-highest cell #{iroot}"
    else:
        iroot = 0
        if root_marker:
            root_note = (f"root_marker {root_marker!r} not in data; "
                         "fell back to cell #0")
        else:
            root_note = "root_marker empty; using cell #0"
    adata.uns["iroot"] = iroot

    ds_dir = WS_ROOT / args["dataset_id"] / "pseudotime"
    ds_dir.mkdir(parents=True, exist_ok=True)

    if (dyn_top_n > 0 or branch_top_n > 0) \
            and "highly_variable" not in adata.var:
        fail("INVALID_INPUT",
             "processed.h5ad lacks highly_variable column; "
             "run sc_process first (or set dyn_top_n=branch_top_n=0)")
        return

    if trajectory_full:
        # Phase 59 轨迹全景：忽略 engine，同一 iroot 锚定串跑
        # palantir 相（pt+分支，branch_top_n 缺省升 50）→ slingshot 相
        # → 谱系×分支交叉（双向产物齐备必触发）→ paga 跟随 flags；
        # 两相 obs 写回（palantir_branch/slingshot_lineage/lineage_branch）
        # 末尾统一落盘一次
        if "X_pca" not in adata.obsm:
            fail("INVALID_INPUT",
                 "processed.h5ad lacks X_pca; run sc_process first")
            return
        bt = branch_top_n if branch_top_n > 0 else 50
        out_p, pt_p = _run_palantir(adata, iroot, clusters, dyn_top_n,
                                    bt, ds_dir,
                                    dyn_modules_k=dyn_modules_k,
                                    modules_enrich=modules_enrich)
        out_s, _pt_s = _run_slingshot(adata, iroot, clusters, root_mode,
                                      dyn_top_n, ds_dir,
                                      dyn_modules_k=dyn_modules_k,
                                      modules_enrich=modules_enrich)
        if paga:
            out_p.update(_run_paga(adata, pt_p, clusters, ds_dir, paga_pt,
                                   root_cluster))
        pst_wide = _load_pst_wide(ds_dir, adata)
        if pst_wide is not None \
                and "palantir_branch" in adata.obs.columns:
            out_s.update(_branch_lineage_cross(ds_dir, adata, pst_wide,
                                               "trajectory_full"))
        adata.write_h5ad(WS_ROOT / args["dataset_id"]
                         / "processed.h5ad")
        stats = _cluster_stats(clusters, pt_p)
        emit({
            "ok": True,
            "dataset_ref": args["dataset_id"],
            "method": "trajectory_full",
            "engine": "trajectory_full",
            "trajectory_full": True,
            "engine_note": "engine param ignored in trajectory_full mode",
            "n_cells": int(adata.n_obs),
            "root_marker": root_marker,
            "root_cluster": root_cluster,
            "root_mode": root_mode,
            "root_cell_index": iroot,
            "root_note": root_note,
            "palantir": out_p,
            "slingshot": out_s,
            "per_cluster": [
                {"cluster": c, "mean": _rf(r["mean"]),
                 "median": _rf(r["median"]), "n_cells": int(r["size"])}
                for c, r in stats.iterrows()],
        })
        return

    if engine == "slingshot":
        out, pt = _run_slingshot(adata, iroot, clusters, root_mode,
                                 dyn_top_n, ds_dir,
                                 dyn_modules_k=dyn_modules_k,
                                 modules_enrich=modules_enrich)
        if paga:
            out.update(_run_paga(adata, pt, clusters, ds_dir, paga_pt,
                                 root_cluster))
        pst_wide = _load_pst_wide(ds_dir, adata)
        if pst_wide is not None \
                and "palantir_branch" in adata.obs.columns:
            out.update(_branch_lineage_cross(ds_dir, adata, pst_wide,
                                             "slingshot"))
        adata.write_h5ad(WS_ROOT / args["dataset_id"]
                         / "processed.h5ad")
        stats = _cluster_stats(clusters, pt)
        emit({
            "ok": True,
            "dataset_ref": args["dataset_id"],
            "method": "slingshot",
            "engine": engine,
            "n_cells": int(adata.n_obs),
            "root_marker": root_marker,
            "root_cluster": root_cluster,
            "root_mode": root_mode,
            "root_cell_index": iroot,
            "root_note": root_note,
            **out,
            "per_cluster": [
                {"cluster": c, "mean": _rf(r["mean"]),
                 "median": _rf(r["median"]), "n_cells": int(r["size"])}
                for c, r in stats.iterrows()],
        })
        return

    if engine == "monocle3":
        if "X_pca" not in adata.obsm:
            fail("INVALID_INPUT",
                 "processed.h5ad lacks X_pca; run sc_process first")
            return
        out, pt = _run_monocle3(adata, iroot, dyn_top_n, ds_dir,
                                dyn_modules_k=dyn_modules_k,
                                modules_enrich=modules_enrich,
                                graph_top_n=graph_top_n)
        if paga:
            out.update(_run_paga(adata, pt, clusters, ds_dir, paga_pt,
                                 root_cluster))
        adata.write_h5ad(WS_ROOT / args["dataset_id"]
                         / "processed.h5ad")
        stats = _cluster_stats(clusters, pt)
        emit({
            "ok": True,
            "dataset_ref": args["dataset_id"],
            "method": "monocle3",
            "engine": engine,
            "n_cells": int(adata.n_obs),
            "root_marker": root_marker,
            "root_cluster": root_cluster,
            "root_mode": root_mode,
            "root_cell_index": iroot,
            "root_note": root_note,
            **out,
            "per_cluster": [
                {"cluster": c, "mean": _rf(r["mean"]),
                 "median": _rf(r["median"]), "n_cells": int(r["size"])}
                for c, r in stats.iterrows()],
        })
        return

    if engine == "palantir":
        if "X_pca" not in adata.obsm:
            fail("INVALID_INPUT",
                 "processed.h5ad lacks X_pca; run sc_process first")
            return
        out, pt = _run_palantir(adata, iroot, clusters, dyn_top_n,
                                branch_top_n, ds_dir,
                                dyn_modules_k=dyn_modules_k,
                                modules_enrich=modules_enrich)
        if paga:
            out.update(_run_paga(adata, pt, clusters, ds_dir, paga_pt,
                                 root_cluster))
        pst_wide = _load_pst_wide(ds_dir, adata)
        cross = None
        if pst_wide is not None \
                and "palantir_branch" in adata.obs.columns:
            cross = _branch_lineage_cross(ds_dir, adata, pst_wide,
                                          "palantir")
            out.update(cross)
        # obs 新增列（分支/谱系标签/paga_dpt）→ 统一落盘
        if branch_top_n > 0 or paga_pt or cross is not None:
            adata.write_h5ad(WS_ROOT / args["dataset_id"]
                             / "processed.h5ad")
        stats = _cluster_stats(clusters, pt)
        emit({
            "ok": True,
            "dataset_ref": args["dataset_id"],
            "method": "palantir",
            "engine": engine,
            "n_cells": int(adata.n_obs),
            "root_marker": root_marker,
            "root_cluster": root_cluster,
            "root_mode": root_mode,
            "root_cell_index": iroot,
            "root_note": root_note,
            **out,
            "per_cluster": [
                {"cluster": c, "mean": _rf(r["mean"]),
                 "median": _rf(r["median"]), "n_cells": int(r["size"])}
                for c, r in stats.iterrows()],
        })
        return

    sc.tl.diffmap(adata)
    sc.tl.dpt(adata)
    pt = adata.obs["dpt_pseudotime"].to_numpy(dtype=float)
    n_inf = int(np.sum(~np.isfinite(pt)))
    pt = np.where(np.isfinite(pt), pt, np.nan)  # inf（不连通）→ nan

    stats = _cluster_stats(clusters, pt)

    pt_df = pd.DataFrame({"leiden": clusters.values, "dpt_pseudotime": pt},
                         index=adata.obs_names)
    pt_csv = ds_dir / "pseudotime.csv"
    pt_df.to_csv(pt_csv)

    # UMAP 伪时序着色（红圈 = root）
    umap = np.asarray(adata.obsm["X_umap"])
    fig, ax = plt.subplots(figsize=(5.2, 4.2))
    s = ax.scatter(umap[:, 0], umap[:, 1], s=4, c=pt, cmap="viridis",
                   linewidths=0)
    ax.scatter(umap[iroot, 0], umap[iroot, 1], s=90, facecolors="none",
               edgecolors="red", linewidths=1.6, label="root")
    ax.legend(loc="upper right", fontsize=8)
    fig.colorbar(s, ax=ax, fraction=0.046, label="DPT pseudotime")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"Diffusion pseudotime (root: {root_note})", fontsize=9)
    fig.tight_layout()
    umap_png = ds_dir / "pseudotime_umap.png"
    fig.savefig(umap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # PAGA 图：簇 UMAP 质心 + 连接度加权边
    sc.tl.paga(adata, groups="leiden")
    conn = adata.uns["paga"]["connectivities"].toarray()
    cents = np.stack([umap[clusters.values == c].mean(axis=0)
                      for c in stats.index])
    fig, ax = plt.subplots(figsize=(5.2, 4.4))
    ax.scatter(umap[:, 0], umap[:, 1], s=3, c="#d9d9d9", linewidths=0)
    w = conn[conn > 0]
    vmax = float(w.max()) if w.size else 1.0
    for i in range(len(cents)):
        for j in range(i + 1, len(cents)):
            if conn[i, j] > 0:
                ax.plot(*zip(cents[i], cents[j]), color="#3b7dd8",
                        lw=0.6 + 2.4 * conn[i, j] / vmax, alpha=0.75,
                        zorder=2)
    ax.scatter(cents[:, 0], cents[:, 1], s=60, c="#f2a636",
               edgecolors="black", linewidths=0.6, zorder=3)
    for (x, y), c in zip(cents, stats.index):
        ax.annotate(c, (x, y), fontsize=8, ha="center", va="center",
                    zorder=4)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("PAGA (node = leiden cluster centroid)", fontsize=9)
    fig.tight_layout()
    paga_png = ds_dir / "paga_graph.png"
    fig.savefig(paga_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    dyn: dict[str, Any] = {}
    if dyn_top_n > 0:
        dyn = _dyn_genes(adata, pt, dyn_top_n, ds_dir,
                         modules_k=dyn_modules_k,
                         modules_enrich=modules_enrich)
    if paga:
        dyn.update(_run_paga(adata, pt, clusters, ds_dir, paga_pt,
                             root_cluster))
        if paga_pt:  # paga_dpt_pseudotime 已写 obs → 统一落盘
            adata.write_h5ad(WS_ROOT / args["dataset_id"]
                             / "processed.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "method": "diffmap_dpt",
        "engine": engine,
        "n_cells": int(adata.n_obs),
        "root_marker": root_marker,
        "root_cluster": root_cluster,
        "root_mode": root_mode,
        "root_cell_index": iroot,
        "root_note": root_note,
        "n_disconnected": n_inf,
        **dyn,
        "per_cluster": [
            {"cluster": c, "mean": _rf(r["mean"]), "median": _rf(r["median"]),
             "n_cells": int(r["size"])}
            for c, r in stats.iterrows()],
        "pseudotime_csv": str(pt_csv),
        "umap_png": str(umap_png),
        "paga_png": str(paga_png),
    })


if __name__ == "__main__":
    run(main)
