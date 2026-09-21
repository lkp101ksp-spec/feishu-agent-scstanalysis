"""st_metabolism：spot 级 KEGG 代谢通路活性（Phase 75 空间版）。

stdin: {"dataset_id": ..., "method": "aucell"|"mean",
        "groupby": "spatial_domain", "species": "human"|"mouse",
        "top_n": 30}
与 sc 版（metabolism.py）三点差异同 st_genescore（obsm.spatial 校验/
空间着色主图/默认 spatial_domain + 域数 ≥2）；method/species 语义与
sc 版一致（aucell=decoupler AUCell，n_up=前 10% 特征；mean=
score_genes 均值差）。参数校验统一 INVALID_INPUT（sc 版部分
ValueError→SCRIPT_ERROR，st 版收敛显式化）。产物目录 {ds}/
st_metabolism/ 与 sc 版 metabolism/ 互不覆盖。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, fail, load_adata, read_args, run, species_style_guard, upper_gene_map

GENE_SET_DIR = Path("/opt/gene_sets")
MIN_PATHWAY_GENES = 5  # 与 scMetabolism/GSEA min_size 惯例一致
MIN_DOMAINS = 2  # 单域无组间方差，引导换列
SPECIES_LIB = {"human": "kegg.json", "mouse": "kegg_mouse.json"}


def main() -> None:
    """主流程：AUCell/score_genes spot 级打分 → 域均值 + 空间着色图。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    top_n = int(args.get("top_n", 30))
    method = str(args.get("method", "aucell")).strip().lower()
    if method not in ("aucell", "mean"):
        fail("INVALID_INPUT", f"method must be 'aucell' or 'mean'; got {method!r}")
        raise SystemExit(1)
    species = str(args.get("species", "human")).strip().lower()
    if species not in SPECIES_LIB:
        fail("INVALID_INPUT", f"species must be one of {sorted(SPECIES_LIB)}; got {species!r}")
        raise SystemExit(1)
    groupby = str(args.get("groupby", "spatial_domain")).strip()
    if not groupby:
        fail("INVALID_INPUT", "groupby must be a non-empty obs column name")
        raise SystemExit(1)

    lib_path = GENE_SET_DIR / SPECIES_LIB[species]
    if not lib_path.exists():
        raise FileNotFoundError(
            f"{lib_path} not found; rebuild bio image with gene_sets stage "
            "(docker build ... sandbox/bio.Dockerfile)")
    pathways: dict[str, list[str]] = json.loads(
        lib_path.read_text(encoding="utf-8"))

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    sp = np.asarray(adata.obsm.get("spatial", []))
    if sp.ndim != 2 or sp.shape[1] < 2 or sp.shape[0] != adata.n_obs:
        fail("INVALID_INPUT",
             "缺 obsm['spatial']（需 (n,≥2) 空间坐标）；本工具为 spot 级"
             "空间版，普通 sc 数据请用 sc_metabolism")
        raise SystemExit(1)
    species_style_guard(species, adata.var_names, "SC_SPECIES_MISMATCH")
    if groupby not in adata.obs:
        fail("INVALID_INPUT",
             f"processed.h5ad lacks {groupby!r}; run st_process first")
        raise SystemExit(1)
    clusters = adata.obs[groupby].astype(str)
    if clusters.nunique() < MIN_DOMAINS:
        fail("INVALID_INPUT",
             f"groupby {groupby!r} 仅 {clusters.nunique()} 个域（<{MIN_DOMAINS} 无组间方差）；"
             "spatial 数据建议 spatial_domain，或显式传其它注释列")
        raise SystemExit(1)

    raw_vars = set(adata.raw.var_names)

    # mouse 库符号全大写，数据 var 是 Mki67 式，upper_gene_map 对齐；
    # human 精确匹配 + 保序去重（Enrichr json 通路内偶见重复基因）
    def _matched(genes: list[str]) -> list[str]:
        """通路基因与数据 var 对齐：mouse 大写映射 / human 精确匹配。"""
        if species == "mouse":
            # str() 收口：mypy 跨模块解析 common 不可见时退化为 Any
            return [str(g) for g in upper_gene_map(sorted(raw_vars), genes)]
        return list(dict.fromkeys(g for g in genes if g in raw_vars))

    terms: list[str] = []
    note: str
    if method == "aucell":
        import decoupler as dc

        net_rows: list[dict[str, object]] = []
        for term, genes in pathways.items():
            matched = _matched(genes)
            if len(matched) < MIN_PATHWAY_GENES:
                continue
            terms.append(term)
            net_rows += [{"source": term, "target": g, "weight": 1.0}
                         for g in matched]
        if not terms:
            raise ValueError(
                f"no KEGG pathway has >= {MIN_PATHWAY_GENES} genes in data")
        # AUCell 排名截断：skill 口径前 10% 特征（decoupler 2.x 默认 top
        # 5%，显式传参覆盖；tmin 与 MIN_PATHWAY_GENES 对齐防 prune 丢通路）
        n_up = int(np.ceil(0.1 * len(raw_vars)))
        dc.mt.aucell(adata, pd.DataFrame(net_rows),
                     tmin=MIN_PATHWAY_GENES, raw=True, n_up=n_up,
                     verbose=False)
        scores_df = adata.obsm["score_aucell"]
        terms = list(scores_df.columns)
        mat = scores_df.to_numpy(dtype=float)  # spot × 通路
        note = (f"AUCell 排名打分（n_up=前10%特征={n_up}）；"
                "method='mean' 回 score_genes 均值差口径")
    else:
        # 逐通路打分（列名用短代号，防 obs 列名过长）
        cols: list[str] = []
        for i, (term, genes) in enumerate(pathways.items()):
            matched = _matched(genes)
            if len(matched) < MIN_PATHWAY_GENES:
                continue
            col = f"pw_{i:03d}"
            sc.tl.score_genes(adata, gene_list=matched, score_name=col,
                              use_raw=True)
            terms.append(term)
            cols.append(col)
        if not terms:
            raise ValueError(
                f"no KEGG pathway has >= {MIN_PATHWAY_GENES} genes in data")
        mat = adata.obs[cols].to_numpy(dtype=float)  # spot × 通路
        note = "均值差口径（score_genes）"

    ds_dir = WS_ROOT / args["dataset_id"] / "st_metabolism"
    ds_dir.mkdir(parents=True, exist_ok=True)

    # spot×通路全矩阵（只落 csv，不进 JSON）
    full = pd.DataFrame(mat, index=adata.obs_names, columns=terms)
    full.insert(0, groupby, clusters.values)
    scores_csv = ds_dir / "st_metabolism_scores.csv"
    full.to_csv(scores_csv)

    # 域 × 通路均值矩阵
    cm = pd.DataFrame(mat, columns=terms)
    cm.insert(0, groupby, clusters.values)
    group_mean = cm.groupby(groupby).mean()
    order = sorted(group_mean.index, key=lambda c: (len(c), c))
    group_mean = group_mean.loc[order]
    mean_csv = ds_dir / "st_metabolism_group_mean.csv"
    group_mean.to_csv(mean_csv)

    # 域间方差 top_n → 热图（通路行 z-score）
    variances = group_mean.var(axis=0)
    top = variances.sort_values(ascending=False).head(top_n)
    top_terms = list(top.index)
    hm = group_mean[top_terms].to_numpy(dtype=float)
    mu = hm.mean(axis=1, keepdims=True)
    sd = hm.std(axis=1, keepdims=True)
    z = np.divide(hm - mu, sd, out=np.zeros_like(hm), where=sd > 0)
    fig, ax = plt.subplots(figsize=(8, 0.28 * len(top_terms) + 1.6))
    im = ax.imshow(z, aspect="auto", cmap="RdBu_r", vmin=-2, vmax=2)
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels(order, fontsize=9)
    ax.set_yticks(range(len(top_terms)))
    ax.set_yticklabels([t[:58] for t in top_terms], fontsize=7)
    fig.colorbar(im, ax=ax, fraction=0.03, label="z-score (per pathway)")
    ax.set_title(f"st metabolism: top {len(top_terms)} variable KEGG pathways",
                 fontsize=10)
    fig.tight_layout()
    heatmap_png = ds_dir / "st_metabolism_heatmap.png"
    fig.savefig(heatmap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 空间着色主图（st 版差异②）：方差 top1 通路 × obsm.spatial
    top_idx = terms.index(top_terms[0])
    fig, ax = plt.subplots(figsize=(5, 4))
    s = ax.scatter(sp[:, 0], sp[:, 1], s=7, c=mat[:, top_idx],
                   cmap="viridis", linewidths=0)
    fig.colorbar(s, ax=ax, fraction=0.046)
    ax.set_title(top_terms[0][:60], fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    spatial_png = ds_dir / "st_metabolism_spatial.png"
    fig.savefig(spatial_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    umap_png = None
    if "X_umap" in adata.obsm:
        umap = np.asarray(adata.obsm["X_umap"])
        fig, ax = plt.subplots(figsize=(5, 4))
        s = ax.scatter(umap[:, 0], umap[:, 1], s=4, c=mat[:, top_idx],
                       cmap="viridis", linewidths=0)
        fig.colorbar(s, ax=ax, fraction=0.046)
        ax.set_title(top_terms[0][:60], fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.tight_layout()
        umap_png = ds_dir / "st_metabolism_umap.png"
        fig.savefig(umap_png, dpi=150, bbox_inches="tight")
        plt.close(fig)

    top_by_group = {
        c: group_mean.loc[c].sort_values(ascending=False).head(3).index.tolist()
        for c in order
    }
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "species": species,
        "method": method,
        "groupby": groupby,
        "method_note": note,
        "n_spots": int(adata.n_obs),
        "n_pathways_total": len(pathways),
        "n_pathways_scored": len(terms),
        "n_domains": int(clusters.nunique()),
        "top_by_group": top_by_group,
        "products": {
            "scores_csv": str(scores_csv),
            "group_mean_csv": str(mean_csv),
            "heatmap_png": str(heatmap_png),
            "spatial_png": str(spatial_png),
            "umap_png": str(umap_png) if umap_png else None,
        },
    })


if __name__ == "__main__":
    run(main)
