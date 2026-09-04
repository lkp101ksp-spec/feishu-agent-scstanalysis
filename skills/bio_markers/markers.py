"""单细胞 markers 提取：按 leiden 簇 wilcoxon 差异分析，出每簇 top markers。"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import scanpy as sc


def main() -> None:
    """按 leiden 簇 rank_genes_groups（use_raw=True），组装每簇 top markers。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_path", required=True, help="processed h5ad（须含 leiden）")
    ap.add_argument("--output_dir", required=True, help="输出目录（dotplot 落此处）")
    ap.add_argument("--method", default="wilcoxon")
    ap.add_argument("--top_n", type=int, default=10)
    args = ap.parse_args()

    inp = Path(args.input_path)
    if not inp.is_file():
        print(json.dumps({"ok": False, "error": f"input not found: {inp}"}))
        return

    adata = sc.read_h5ad(inp)
    if "leiden" not in adata.obs.columns:
        print(json.dumps({"ok": False, "error": "no leiden column, run preprocess first"}))
        return

    sc.tl.rank_genes_groups(adata, groupby="leiden", method=args.method, use_raw=True)

    markers = {}
    rgg = adata.uns["rank_genes_groups"]
    for cluster in rgg["names"].dtype.names:
        genes = list(rgg["names"][cluster])[: args.top_n]
        scores = list(rgg["scores"][cluster])[: args.top_n]
        logfcs = list(rgg["logfoldchanges"][cluster])[: args.top_n]
        markers[str(cluster)] = [
            {"gene": g, "score": round(float(s), 2), "log2fc": round(float(l), 2)}
            for g, s, l in zip(genes, scores, logfcs)
        ]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dotplot_path = out_dir / "markers_dotplot.png"
    sc.pl.rank_genes_groups_dotplot(adata, n_genes=5, standard_scale="var",
                                    show=False, save=False)
    matplotlib.pyplot.savefig(dotplot_path, bbox_inches="tight", dpi=120)
    matplotlib.pyplot.close("all")

    print(json.dumps({
        "ok": True,
        "method": args.method,
        "n_clusters": int(adata.obs["leiden"].nunique()),
        "markers": markers,
        "dotplot": str(dotplot_path),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
