"""sc_enrichment：DEG→ORA（多基因集）+ GSEA prerank（Phase 31 富集分析）。

stdin: {"dataset_id": ..., "group": "3",
        "gene_sets": ["hallmark", "go_bp", "kegg"],
        "top_n": 15, "min_log2fc": 0.25, "rank_method": "wilcoxon"}
需 processed.h5ad（sc_process 产物）。
基因集从 /opt/gene_sets/<key>.json 读（镜像构建期 Enrichr 预取；
运行期容器 --network none 离线可用，对齐 clusterProfiler ORA+GSEA 能力）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from common import WS_ROOT, emit, load_adata, read_args, run

# 工具参数别名 → (Enrichr 库名, /opt/gene_sets 文件名)
GS_KEYS = {
    "hallmark": ("MSigDB_Hallmark_2020", "hallmark.json"),
    "go_bp": ("GO_Biological_Process_2023", "go_bp.json"),
    "kegg": ("KEGG_2021_Human", "kegg.json"),
    "kegg_mouse": ("KEGG_2019_Mouse", "kegg_mouse.json"),
    "wikipathways_mouse": ("WikiPathways_2019_Mouse",
                           "wikipathways_mouse.json"),
}
GENE_SET_DIR = Path("/opt/gene_sets")
ORA_MAX_GENES = 300


def _load_lib(alias: str) -> dict[str, list[str]]:
    """读镜像内预取基因集 JSON；缺失说明镜像未重建，给出明确提示。"""
    _, fname = GS_KEYS[alias]
    p = GENE_SET_DIR / fname
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found; rebuild bio image with gene_sets stage "
            "(docker build ... sandbox/bio.Dockerfile)")
    return cast(dict[str, list[str]],
                json.loads(p.read_text(encoding="utf-8")))


def main() -> None:
    """主流程：rank_genes_groups 出 DEG → ORA + GSEA → csv/png 落 workspace。"""
    import gseapy as gp
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import scanpy as sc

    args = read_args()
    aliases = [a for a in args.get("gene_sets") or list(GS_KEYS)
               if a in GS_KEYS]
    if not aliases:
        raise ValueError(f"no valid gene_sets in {args.get('gene_sets')}")
    top_n = int(args.get("top_n", 15))
    min_log2fc = float(args.get("min_log2fc", 0.25))
    method = str(args.get("rank_method", "wilcoxon"))

    adata = load_adata({"dataset_id": args["dataset_id"],
                        "file": "processed"})
    sc.tl.rank_genes_groups(adata, groupby="leiden", method=method,
                            use_raw=True)
    result = adata.uns["rank_genes_groups"]
    groups = [str(g) for g in result["names"].dtype.names]
    group = str(args.get("group") or groups[0])
    if group not in groups:
        raise ValueError(
            f"group {group!r} not in leiden clusters {groups}")

    # ORA 输入：目标簇上调基因（log2fc 阈值，上限截断）
    logfc = np.asarray(result["logfoldchanges"][group], dtype=float)
    names = np.asarray([str(x) for x in result["names"][group]])
    up = [g for g, lf in zip(names, logfc) if lf >= min_log2fc]
    if len(up) < 5:
        raise ValueError(
            f"only {len(up)} up-regulated genes (log2fc>={min_log2fc}); "
            f"lower min_log2fc or check cluster {group}")
    up = up[:ORA_MAX_GENES]

    ds_dir = WS_ROOT / args["dataset_id"] / "enrichment"
    ds_dir.mkdir(parents=True, exist_ok=True)

    ora_rows: list[pd.DataFrame] = []
    gsea_rows: list[pd.DataFrame] = []
    for alias in aliases:
        lib = _load_lib(alias)
        # 小鼠库符号全大写（ABCA2 式），DEG 基因名需 .upper() 对齐；人源库不动
        is_mouse = alias.endswith("_mouse")
        ora_input = [g.upper() for g in up] if is_mouse else up
        er = gp.enrich(gene_list=ora_input, gene_sets=lib, outdir=None,
                       verbose=False).results
        df = pd.DataFrame(er)
        df = df[df["Adjusted P-value"] < 1.0].sort_values("Adjusted P-value")
        df.insert(0, "gene_set", alias)
        ora_rows.append(df)

        rnk_genes = [g.upper() for g in names] if is_mouse else names
        rnk = pd.DataFrame({"gene": rnk_genes, "score": np.asarray(
            result["scores"][group], dtype=float)}).sort_values(
            "score", ascending=False)
        pre = gp.prerank(rnk=rnk, gene_sets=lib, outdir=None,
                         min_size=5, max_size=500, verbose=False)
        gdf = pre.res2d.copy()
        gdf["gene_set"] = alias
        gsea_rows.append(gdf)

    ora_df = pd.concat(ora_rows, ignore_index=True)
    gsea_df = pd.concat(gsea_rows, ignore_index=True)
    ora_csv = ds_dir / "ora.csv"
    gsea_csv = ds_dir / "gsea.csv"
    ora_df.to_csv(ora_csv, index=False)
    gsea_df.to_csv(gsea_csv, index=False)

    # ORA 柱状图（每基因集一子图，-log10 adjP）
    n_panels = len(aliases)
    fig, axes = plt.subplots(n_panels, 1, figsize=(9, 3.2 * n_panels))
    axes = np.atleast_1d(axes)
    for ax, alias in zip(axes, aliases):
        sub = ora_df[ora_df["gene_set"] == alias].head(top_n)
        y = np.arange(len(sub))[::-1]
        vals = -np.log10(np.maximum(sub["Adjusted P-value"].to_numpy(), 1e-12))
        ax.barh(y, vals, color="#3b7dd8")
        ax.set_yticks(y)
        ax.set_yticklabels([t[:60] for t in sub["Term"]], fontsize=8)
        ax.set_xlabel("-log10(Adjusted P)")
        ax.set_title(f"ORA: {alias} (n={len(up)} genes)", fontsize=10)
    fig.tight_layout()
    ora_png = ds_dir / "ora_bar.png"
    fig.savefig(ora_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # GSEA NES 图（|NES| 排序，红正蓝负）
    fig, axes = plt.subplots(n_panels, 1, figsize=(9, 3.2 * n_panels))
    axes = np.atleast_1d(axes)
    for ax, alias in zip(axes, aliases):
        sub = gsea_df[gsea_df["gene_set"] == alias].copy()
        sub["NESf"] = sub["NES"].astype(float).abs()
        sub = sub.sort_values("NESf", ascending=False).head(top_n)
        y = np.arange(len(sub))[::-1]
        colors = ["#d84b3b" if float(v) > 0 else "#3b7dd8"
                  for v in sub["NES"]]
        ax.barh(y, sub["NES"].astype(float), color=colors)
        ax.set_yticks(y)
        ax.set_yticklabels([t[:60] for t in sub["Term"]], fontsize=8)
        ax.set_xlabel("NES (red=activated, blue=suppressed)")
        ax.set_title(f"GSEA: {alias}", fontsize=10)
    fig.tight_layout()
    gsea_png = ds_dir / "gsea_nes.png"
    fig.savefig(gsea_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    def _ora_top(alias: str) -> list[dict[str, Any]]:
        sub = ora_df[ora_df["gene_set"] == alias].head(top_n)
        return [
            {"term": r["Term"], "adj_p": round(float(r["Adjusted P-value"]), 5),
             "odds_ratio": round(float(r["Odds Ratio"]), 2),
             "genes": str(r["Genes"])[:200]}
            for _, r in sub.iterrows()]

    def _gsea_top(alias: str) -> list[dict[str, Any]]:
        sub = gsea_df[gsea_df["gene_set"] == alias].copy()
        sub["NESf"] = sub["NES"].astype(float).abs()
        sub = sub.sort_values("NESf", ascending=False).head(top_n)
        return [
            {"term": r["Term"], "nes": round(float(r["NES"]), 3),
             "fdr_q": round(float(r["FDR q-val"]), 5)}
            for _, r in sub.iterrows()]

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "group": group,
        "n_ora_genes": len(up),
        "gene_sets": aliases,
        "ora": {a: _ora_top(a) for a in aliases},
        "gsea": {a: _gsea_top(a) for a in aliases},
        "ora_csv": str(ora_csv),
        "gsea_csv": str(gsea_csv),
        "ora_png": str(ora_png),
        "gsea_png": str(gsea_png),
    })


if __name__ == "__main__":
    run(main)
