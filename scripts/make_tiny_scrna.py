"""生成 tiny scRNA 参考 h5ad（300 细胞 × 190 基因，3 细胞类型对应
tiny_visium 3 空间域；批③ cell2location 反卷积真机串联用）。

基因集与 make_tiny_visium 完全一致（MARKER_D1/D2/D3 各 20 + BG 120 +
LR 6 + MT 4）：CT_A/CT_B/CT_C 分别高表达 MARKER_D1/D2/D3，LR 基因分布
与对应空间域一致（CXCL12@CT_A、CXCR4@CT_B、VEGFA@CT_B、KDR@CT_C、
CSF1@CT_C、CSF1R@CT_A）——反卷积应还原 D0 域 CT_A 占优的空间格局。

用法：python scripts/make_tiny_scrna.py <输出 h5ad 路径>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> None:
    """生成 300x190 泊松计数矩阵（celltype 标记 + 类型特异表达）并写 h5ad。"""
    out = Path(sys.argv[1] if len(sys.argv) > 1 else
               "bio_test_data/tiny_scrna.h5ad")
    out.parent.mkdir(parents=True, exist_ok=True)
    n_marker, n_bg = 20, 120
    rng = np.random.default_rng(7)

    # 基因顺序与 make_tiny_visium 完全一致：
    # MARKER 60 + BG 120 + LR 6（配体受体交替）+ MT 4 = 190
    genes = ([f"MARKER_D{d+1}_{i}" for d in range(3) for i in range(n_marker)]
             + [f"BG_{i}" for i in range(n_bg)]
             + ["CXCL12", "CXCR4", "VEGFA", "KDR", "CSF1", "CSF1R"]
             + [f"MT-{i}" for i in range(1, 5)])

    n_cells, n_types = 300, 3
    ctype = np.repeat([f"CT_{chr(65 + t)}" for t in range(n_types)],
                      n_cells // n_types)

    # 背景泊松 0.3 → 类型 t 高表达 MARKER_D{t+1} 家族（泊松 8）
    X = rng.poisson(0.3, (n_cells, len(genes))).astype(np.float32)
    for t in range(n_types):
        rows = ctype == f"CT_{chr(65 + t)}"
        cols = np.arange(t * n_marker, (t + 1) * n_marker)
        X[np.ix_(rows, cols)] += rng.poisson(
            8.0, (rows.sum(), n_marker)).astype(np.float32)

    # LR 基因方向性（与空间域一致）：指定类型行泊松 6 叠加
    lr_map = {"CXCL12": 0, "CXCR4": 1, "VEGFA": 1, "KDR": 2,
              "CSF1": 2, "CSF1R": 0}
    for gene, t in lr_map.items():
        col = genes.index(gene)
        rows = ctype == f"CT_{chr(65 + t)}"
        X[rows, col] += rng.poisson(6.0, int(rows.sum())).astype(np.float32)

    import anndata as ad

    obs = pd.DataFrame({"celltype": ctype.astype(str)},
                       index=[f"cell{i}" for i in range(n_cells)])
    a = ad.AnnData(X=X, obs=obs, var=pd.DataFrame(index=genes))
    a.write_h5ad(out)
    counts = pd.Series(ctype).value_counts().sort_index().tolist()
    print(f"tiny scrna written: {out} ({n_cells} x {len(genes)}, "
          f"3 types {counts})")


if __name__ == "__main__":
    main()
