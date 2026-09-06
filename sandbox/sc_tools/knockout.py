"""sc_knockout：虚拟敲除（Phase 37，R 包 scTenifoldKnk 保真路线）。

stdin: {"dataset_id": ..., "gko": "SPI1", "celltype_col": "",
        "group": "", "n_genes": 1000, "n_net": 10, "n_cells": 500,
        "min_lib_size": 1000, "mt_threshold": 0.1}
counts 走 filtered/raw 链；可按 celltype_col==group 子集（obs 取自
processed，交集对齐）；HVG top n_genes → dense CSV → 容器内 Rscript
跑 knk.R（scTenifoldKnk：pcNet→张量分解→流形对齐→dRegulation）→
读回 diffRegulation.csv → 火山图。产物落 knockout/。耗时分钟~小时级
（nNet 次网络构建是重头）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
from common import WS_ROOT, emit, load_adata, read_args, run

R_SCRIPT = Path("/opt/r_tools/knk.R")


def main() -> None:
    """主流程：子集+HVG → dense CSV → Rscript knk → 读回 + 火山图。"""
    import matplotlib.pyplot as plt
    import scanpy as sc

    args = read_args()
    gko = str(args["gko"]).strip()
    n_genes = int(args.get("n_genes", 1000))
    celltype_col = str(args.get("celltype_col", "")).strip()
    group = str(args.get("group", "")).strip()

    counts_ad = load_adata({"dataset_id": args["dataset_id"],
                            "file": "any"})
    if celltype_col and group:
        proc = load_adata({"dataset_id": args["dataset_id"],
                           "file": "processed"})
        if celltype_col not in proc.obs:
            raise ValueError(f"celltype column {celltype_col!r} not in "
                             f"processed obs")
        keep = proc.obs_names[proc.obs[celltype_col].astype(str) == group]
        cells = counts_ad.obs_names.intersection(keep)
        if len(cells) < 100:
            raise ValueError(f"group {group!r} has {len(cells)} cells "
                             f"(<100)")
        counts_ad = counts_ad[cells]

    # HVG：lognorm 副本上选，矩阵仍用 counts（scTenifoldKnk 输入为 counts）
    norm = counts_ad.copy()
    sc.pp.normalize_total(norm, target_sum=1e4)
    sc.pp.log1p(norm)
    sc.pp.highly_variable_genes(norm, n_top_genes=min(n_genes, norm.n_vars),
                                flavor="seurat")
    genes = norm.var_names[norm.var["highly_variable"]].astype(str)
    if gko not in genes:
        near = [g for g in norm.var_names.astype(str)
                if g.upper() == gko.upper() or gko.upper() in g.upper()]
        raise ValueError(f"gKO {gko!r} not in HVG set (n={len(genes)}); "
                         f"near matches in data: {near[:10]}")
    sub = counts_ad[:, genes]
    X = sub.X.toarray() if hasattr(sub.X, "toarray") else np.asarray(sub.X)
    mat = pd.DataFrame(X.T, index=genes, columns=sub.obs_names.astype(str))

    out_dir = WS_ROOT / args["dataset_id"] / "knockout" / gko
    out_dir.mkdir(parents=True, exist_ok=True)
    input_csv = out_dir / "input_counts.csv"
    mat.to_csv(input_csv)

    r_cmd = [
        "Rscript", str(R_SCRIPT), str(input_csv), str(out_dir), gko,
        str(int(args.get("n_net", 10))),
        str(min(int(args.get("n_cells", 500)), mat.shape[1])),
        str(int(args.get("min_lib_size", 1000))),
        str(float(args.get("mt_threshold", 0.1))),
    ]
    proc_r = subprocess.run(r_cmd, capture_output=True, text=True,
                            timeout=3300)
    if proc_r.returncode != 0:
        raise RuntimeError(f"Rscript knk.R failed: {proc_r.stderr[-1500:]}")
    dr_path = out_dir / "diffRegulation.csv"
    if not dr_path.exists():
        raise RuntimeError(f"knk.R did not produce diffRegulation.csv; "
                           f"stdout tail: {proc_r.stdout[-500:]}")

    dr = pd.read_csv(dr_path)
    dr.columns = [c.strip() for c in dr.columns]
    gene_col = next((c for c in dr.columns if c.lower() in
                     ("gene", "genes", "x")), dr.columns[0])
    z_col = next(c for c in dr.columns if c.lower() == "z")
    p_col = next((c for c in dr.columns if c.lower() in
                  ("p.adj", "padj", "p_adj")), None)
    dr = dr.rename(columns={gene_col: "gene"})
    dr = dr.sort_values(z_col, ascending=False)

    fig, ax = plt.subplots(figsize=(6.5, 5))
    y = -np.log10(dr[p_col].clip(lower=1e-300)) if p_col \
        else dr[z_col]
    ax.scatter(dr[z_col], y, s=6, c="#888888", linewidths=0)
    top = dr.head(10)
    ax.scatter(top[z_col],
               (-np.log10(top[p_col].clip(lower=1e-300)) if p_col
                else top[z_col]),
               s=14, c="#d84b3b", linewidths=0)
    for _, row in top.iterrows():
        ax.annotate(str(row["gene"]),
                    (row[z_col],
                     -np.log10(max(row[p_col], 1e-300)) if p_col
                     else row[z_col]),
                    fontsize=6, alpha=0.8)
    ax.set_xlabel("dRegulation Z")
    ax.set_ylabel("-log10(p.adj)" if p_col else "Z")
    ax.set_title(f"virtual KO: {gko} ({mat.shape[1]} cells, "
                 f"{mat.shape[0]} genes)", fontsize=10)
    fig.tight_layout()
    png_path = out_dir / "knockout_volcano.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "gko": gko,
        "n_cells": int(mat.shape[1]),
        "n_genes": int(mat.shape[0]),
        "group": f"{celltype_col}=={group}" if group else "",
        "top_dr_genes": [str(g) for g in dr["gene"].head(10)],
        "dr_csv": str(dr_path),
        "volcano_png": str(png_path),
        "note": "scTenifoldKnk R 包保真链路；diffRegulation 全表见 csv",
    })


if __name__ == "__main__":
    run(main)
