"""sc_metabolism：KEGG 代谢通路活性打分（Phase 32/73，对齐 scMetabolism）。

stdin: {"dataset_id": ..., "top_n": 30, "species": "human"|"mouse",
        "method": "aucell"|"mean", "groupby": "leiden"}
需 processed.h5ad；通路库按 species 选镜像 /opt/gene_sets/kegg.json
（Enrichr KEGG_2021_Human）或 kegg_mouse.json（KEGG_2019_Mouse），
构建期预取，运行期离线可用。
method="aucell"（Phase 73 默认，对齐 scMetabolism 官方 AUCell）：decoupler
逐细胞排名 AUC 打分，n_up=前 10% 特征（skill tool-scmetabolism 口径；
decoupler 2.x 默认 top 5%，2026-09-19 探针钉注后显式传参覆盖）；
method="mean"：Phase 32 逐通路 score_genes 均值差原路径（回归保障）。
细胞×通路全矩阵只落 csv，JSON/热图只带簇均值与簇间方差 top_n 通路（防膨胀）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run, species_style_guard, upper_gene_map

GENE_SET_DIR = Path("/opt/gene_sets")
MIN_PATHWAY_GENES = 5  # 与 scMetabolism/GSEA min_size 惯例一致
SPECIES_LIB = {"human": "kegg.json", "mouse": "kegg_mouse.json"}


def main() -> None:
    """主流程：逐通路 score_genes → 簇×通路均值 + 方差 top 热图/UMAP。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    top_n = int(args.get("top_n", 30))
    method = str(args.get("method", "aucell")).strip().lower()
    if method not in ("aucell", "mean"):
        raise ValueError(f"method must be 'aucell' or 'mean'; got {method!r}")
    species = str(args.get("species", "human")).strip().lower()
    if species not in SPECIES_LIB:
        raise ValueError(
            f"species must be one of {sorted(SPECIES_LIB)}; got {species!r}")
    groupby = str(args.get("groupby", "leiden")).strip()
    if not groupby:
        raise ValueError("groupby must be a non-empty obs column name")

    lib_path = GENE_SET_DIR / SPECIES_LIB[species]
    if not lib_path.exists():
        raise FileNotFoundError(
            f"{lib_path} not found; rebuild bio image with gene_sets stage "
            "(docker build ... sandbox/bio.Dockerfile)")
    pathways: dict[str, list[str]] = json.loads(
        lib_path.read_text(encoding="utf-8"))

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    species_style_guard(species, adata.var_names, "SC_SPECIES_MISMATCH")
    if groupby not in adata.obs:
        raise ValueError(
            f"processed.h5ad lacks {groupby!r}; run sc_process first")

    raw_vars = set(adata.raw.var_names)
    clusters = adata.obs[groupby].astype(str)

    # mouse 库符号全大写（ABCA2 式），数据 var 是 Mki67 式，
    # 用 upper_gene_map 大写对齐并返回原始 var 名；human 保持精确匹配
    def _matched(genes: list[str]) -> list[str]:
        """通路基因与数据 var 对齐：mouse 大写映射 / human 精确匹配。"""
        if species == "mouse":
            # str() 收口：mypy 跨模块解析 common 不可见时 upper_gene_map 退化为 Any
            return [str(g) for g in upper_gene_map(sorted(raw_vars), genes)]
        # Enrichr json 通路内偶见重复基因（KEGG_2021_Human 实证 2026-09-19），
        # 保序去重防 decoupler net 出现 (source,target) 重复行被拒收
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
        # AUCell 排名截断：skill 口径前 10% 特征。decoupler 2.x 的
        # n_up 默认 None→top 5%（2026-09-19 探针钉注源码 _aucell.py），
        # 显式传参覆盖；tmin 与 MIN_PATHWAY_GENES 对齐防 prune 丢通路
        n_up = int(np.ceil(0.1 * len(raw_vars)))
        dc.mt.aucell(adata, pd.DataFrame(net_rows),
                     tmin=MIN_PATHWAY_GENES, raw=True, n_up=n_up,
                     verbose=False)
        scores = adata.obsm["score_aucell"]
        terms = list(scores.columns)
        mat = scores.to_numpy(dtype=float)  # 细胞 × 通路
        note = (f"AUCell 排名打分（Phase 73 默认，n_up=前10%特征={n_up}）；"
                "method='mean' 回 Phase 32 score_genes 均值差口径")
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
        mat = adata.obs[cols].to_numpy(dtype=float)  # 细胞 × 通路
        note = "Phase 32 均值差口径（score_genes）"

    ds_dir = WS_ROOT / args["dataset_id"] / "metabolism"
    ds_dir.mkdir(parents=True, exist_ok=True)

    # 细胞×通路全矩阵（只落 csv，不进 JSON）
    full = pd.DataFrame(mat, index=adata.obs_names, columns=terms)
    full.insert(0, groupby, clusters.values)
    scores_csv = ds_dir / "metabolism_scores.csv"
    full.to_csv(scores_csv)

    # 簇 × 通路均值矩阵
    cm = pd.DataFrame(mat, columns=terms)
    cm.insert(0, groupby, clusters.values)
    cluster_mean = cm.groupby(groupby).mean()
    order = sorted(cluster_mean.index, key=lambda c: (len(c), c))
    cluster_mean = cluster_mean.loc[order]
    mean_csv = ds_dir / "metabolism_cluster_mean.csv"
    cluster_mean.to_csv(mean_csv)

    # 簇间方差 top_n → 热图（通路行 z-score）
    variances = cluster_mean.var(axis=0)
    top = variances.sort_values(ascending=False).head(top_n)
    top_terms = list(top.index)
    hm = cluster_mean[top_terms].to_numpy(dtype=float)
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
    ax.set_title(f"Metabolism: top {len(top_terms)} variable KEGG pathways",
                 fontsize=10)
    fig.tight_layout()
    heatmap_png = ds_dir / "metabolism_heatmap.png"
    fig.savefig(heatmap_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 方差 top1 通路 UMAP 着色（代谢重编程快速定位图）
    top_idx = terms.index(top_terms[0])
    umap_vals = mat[:, top_idx]
    umap_png = None
    if "X_umap" in adata.obsm:
        umap = np.asarray(adata.obsm["X_umap"])
        fig, ax = plt.subplots(figsize=(5, 4))
        s = ax.scatter(umap[:, 0], umap[:, 1], s=4,
                       c=umap_vals,
                       cmap="viridis", linewidths=0)
        fig.colorbar(s, ax=ax, fraction=0.046)
        ax.set_title(top_terms[0][:60], fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.tight_layout()
        umap_png = ds_dir / "metabolism_umap.png"
        fig.savefig(umap_png, dpi=150, bbox_inches="tight")
        plt.close(fig)

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "species": species,
        "method": method,
        "groupby": groupby,
        "method_note": note,
        "n_cells": int(adata.n_obs),
        "n_pathways_total": len(pathways),
        "n_pathways_scored": len(terms),
        "top_pathways": [
            {"term": t, "variance": round(float(top[t]), 6),
             "cluster_means": {c: round(float(cluster_mean.loc[c, t]), 4)
                               for c in order}}
            for t in top_terms],
        "scores_csv": str(scores_csv),
        "cluster_mean_csv": str(mean_csv),
        "heatmap_png": str(heatmap_png),
        "umap_png": str(umap_png) if umap_png else None,
    })


if __name__ == "__main__":
    run(main)
