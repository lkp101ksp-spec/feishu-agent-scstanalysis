"""sc_cellcycle：细胞周期打分（Phase 35，Tirosh 2016 S/G2M 基因集内嵌）。

stdin: {"dataset_id": ..., "celltype_col": "leiden"}
需 processed.h5ad。S/G2M 基因集（Tirosh 2016，Seurat cc.genes 同款）
内嵌常量，断网可用；score_genes_cell_cycle 在 raw（log-norm 全基因）
上打分 → obs 增 S_score/G2M_score/phase，原地写回 processed.h5ad。
产物：phase UMAP + phase×簇计数 csv。基因交集 <5 报错提示物种不匹配
（内置为人源基因名）。
"""
from __future__ import annotations

import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run, upper_gene_map

S_GENES = [
    "MCM5", "PCNA", "TYMS", "FEN1", "MCM2", "MCM4", "RRM1", "UNG",
    "GINS2", "MCM6", "CDCA7", "DTL", "PRIM1", "UHRF1", "HELLS", "RFC2",
    "RPA2", "NASP", "RAD51AP1", "GMNN", "WDR76", "SLBP", "CCNE2", "UBR7",
    "POLD3", "MSH2", "ATAD2", "RAD51", "RRM2", "CDC45", "CDC6", "EXO1",
    "TIPIN", "DSCC1", "BLM", "CASP8AP2", "USP1", "CLSPN", "POLA1",
    "CHAF1B", "BRIP1", "E2F8",
]

G2M_GENES = [
    "HMGB2", "CDK1", "NUSAP1", "UBE2C", "BIRC5", "TPX2", "TOP2A",
    "NDC80", "CKS2", "NUF2", "CKS1B", "MKI67", "TMPO", "CENPF", "TACC3",
    "PIMREG", "SMC4", "CCNB2", "CKAP2L", "CKAP2", "AURKB", "BUB1",
    "KIF11", "ANP32E", "TUBB4B", "GTSE1", "KIF20B", "HJURP", "CDCA3",
    "HN1", "CDC20", "TTK", "CDC25C", "KIF2C", "RANGAP1", "NCAPD2",
    "DLGAP5", "CDCA2", "CDCA8", "ECT2", "KIF23", "HMMR", "AURKA",
    "PSRC1", "ANLN", "LBR", "CKAP5", "CENPE", "CTCF", "NEK2", "G2E3",
    "GAS2L3", "CBX5", "CENPA",
]


def main() -> None:
    """主流程：raw 上打 S/G2M 分 → phase 写回 → UMAP + 计数 csv。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    celltype_col = str(args.get("celltype_col", "leiden")).strip()

    adata = load_adata({"dataset_id": args["dataset_id"], "file": "processed"})
    if adata.raw is None:
        raise ValueError("processed.h5ad missing raw; re-run sc_process")
    raw_genes_list = [str(g) for g in adata.raw.var_names.astype(str)]
    s_found = upper_gene_map(raw_genes_list, S_GENES)
    g_found = upper_gene_map(raw_genes_list, G2M_GENES)
    case_mapped = (any(g not in S_GENES for g in s_found)
                   or any(g not in G2M_GENES for g in g_found))
    if len(s_found) < 5 or len(g_found) < 5:
        raise ValueError(
            f"too few cell-cycle genes found (S={len(s_found)}, "
            f"G2M={len(g_found)}); built-in sets are human gene symbols, "
            "check species / gene naming")

    sc.tl.score_genes_cell_cycle(adata, s_genes=s_found, g2m_genes=g_found,
                                 use_raw=True)

    out_dir = WS_ROOT / args["dataset_id"] / "cellcycle"
    out_dir.mkdir(parents=True, exist_ok=True)
    phase = adata.obs["phase"].astype(str)
    ct = pd.crosstab(adata.obs[celltype_col].astype(str)
                     if celltype_col in adata.obs
                     else pd.Series(["all"] * adata.n_obs),
                     phase)
    csv_path = out_dir / "cellcycle_phase_counts.csv"
    ct.to_csv(csv_path)

    fig, ax = plt.subplots(figsize=(6.5, 5))
    umap = adata.obsm["X_umap"]
    colors = {"G1": "#b8b8b8", "S": "#d84b3b", "G2M": "#3b7dd8"}
    for ph in ("G1", "S", "G2M"):
        m = (phase == ph).to_numpy()
        if m.any():
            ax.scatter(umap[m, 0], umap[m, 1], s=5, c=colors[ph],
                       label=f"{ph} ({int(m.sum())})", linewidths=0)
    ax.legend(fontsize=8, loc="center left", bbox_to_anchor=(1.0, 0.5),
              markerscale=2)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("cell cycle phase (Tirosh S/G2M)", fontsize=10)
    fig.tight_layout()
    png_path = out_dir / "cellcycle_umap.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    h5ad_path = WS_ROOT / args["dataset_id"] / "processed.h5ad"
    adata.write(h5ad_path)
    counts = phase.value_counts()
    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "s_genes_found": len(s_found),
        "g2m_genes_found": len(g_found),
        "case_mapped": case_mapped,
        "phase_counts": {str(k): int(v) for k, v in counts.items()},
        "csv": str(csv_path),
        "umap_png": str(png_path),
        "saved": str(h5ad_path),
        "note": "S_score/G2M_score/phase 已写回 processed.h5ad",
    })


if __name__ == "__main__":
    run(main)
