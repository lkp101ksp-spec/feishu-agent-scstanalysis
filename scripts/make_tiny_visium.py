"""生成 tiny Visium spaceranger 目录（~196 spots 14x14 网格、3 空间域、
域特异基因 MARKER_D1/D2/D3，Phase 21 真机冒烟用）。

用法：python scripts/make_tiny_visium.py <输出目录>
目录结构：filtered_feature_bc_matrix.h5 + spatial/
（tissue_positions_list.csv + scalefactors_json.json）——squidpy
sq.read.visium v2 布局。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "bio_test_data/tiny_visium")
    n_side = 14          # 14x14 网格 → 196 spots
    n_marker = 20        # 每域特异基因数
    n_bg = 120           # 背景基因
    rng = np.random.default_rng(42)

    # 3 空间域：按空间坐标三等分（左/中/右竖条带）
    domains = np.repeat([0, 1, 2], (n_side * 5, n_side * 5, n_side * 4))

    genes = ([f"MARKER_D{d+1}_{i}" for d in range(3) for i in range(n_marker)]
             + [f"BG_{i}" for i in range(n_bg)])
    spots = [f"spot{i}" for i in range(n_side * n_side)]

    # 计数矩阵（泊松）：域特异基因只在自己域高表达
    X = rng.poisson(0.3, (len(spots), len(genes))).astype(np.float32)
    for d in range(3):
        cols = slice(d * n_marker, (d + 1) * n_marker)
        rows = domains == d
        X[np.ix_(rows, np.arange(n_marker * 3)[cols])] += \
            rng.poisson(8.0, (rows.sum(), n_marker)).astype(np.float32)
    # 3 对 CellChat human LR 基因（批② COMMOT 用；环形方向性通讯：
    # 配体高表达域 → 受体高表达域：D1→D2、D2→D3、D3→D1）
    lr_pairs = [("CXCL12", 0, "CXCR4", 1),
                ("VEGFA", 1, "KDR", 2),
                ("CSF1", 2, "CSF1R", 0)]
    lr_cols = []  # (lr_mat 内列号, 高表达域)——列号与 genes 追加顺序一致
    for lig, ld, rec, rd in lr_pairs:
        lr_cols.append((len(lr_cols), ld))
        genes.append(lig)
        lr_cols.append((len(lr_cols), rd))
        genes.append(rec)
    lr_mat = rng.poisson(0.3, (len(spots), len(lr_pairs) * 2)).astype(np.float32)
    for col, dom in lr_cols:
        lr_mat[domains == dom, col] += rng.poisson(
            6.0, int((domains == dom).sum())).astype(np.float32)
    X = np.hstack([X, lr_mat])
    # 4 个 MT 基因（QC 用）
    genes += [f"MT-{i}" for i in range(1, 5)]
    mt = rng.poisson(1.0, (len(spots), 4)).astype(np.float32)
    X = np.hstack([X, mt])

    # 写 h5（spaceranger filtered_feature_bc_matrix.h5 10x 格式）
    import h5py

    out.mkdir(parents=True, exist_ok=True)
    (out / "spatial").mkdir(exist_ok=True)
    barcodes = np.array([s.encode() for s in spots], dtype="S32")
    names = np.array([g.encode() for g in genes], dtype="S32")
    ids = np.array([f"gene{i}".encode() for i in range(len(genes))],
                   dtype="S32")
    feat_type = np.array([b"Gene Expression"] * len(genes), dtype="S32")

    with h5py.File(out / "filtered_feature_bc_matrix.h5", "w") as f:
        g = f.create_group("matrix")
        g.create_dataset("shape", data=np.array([len(genes), len(spots)],
                                                dtype=np.int64))
        # CSC：按列（spot）存，只存非零条目（真实 spaceranger 不存显式零；
        # scanpy 读取时按 (spots×genes) CSR 三元组解释，两种布局等价）
        indptr = np.zeros(len(spots) + 1, dtype=np.int64)
        idx_parts, val_parts = [], []
        for j in range(len(spots)):
            nz = np.nonzero(X[j])[0]
            idx_parts.append(nz.astype(np.int64))
            val_parts.append(X[j, nz])
            indptr[j + 1] = indptr[j] + len(nz)
        indices = np.concatenate(idx_parts)
        data = np.concatenate(val_parts).astype(np.float32)
        g.create_dataset("data", data=data)
        g.create_dataset("indices", data=indices)
        g.create_dataset("indptr", data=indptr)
        g.create_dataset("barcodes", data=barcodes)
        feat = g.create_group("features")
        feat.create_dataset("name", data=names)
        feat.create_dataset("id", data=ids)
        feat.create_dataset("feature_type", data=feat_type)

    # spatial/：tissue_positions_list.csv + scalefactors
    with open(out / "spatial" / "tissue_positions_list.csv", "w") as f:
        for i, s in enumerate(spots):
            r, c = divmod(i, n_side)
            f.write(f"{s},1,{r},{c},{r * 100},{c * 100}\n")
    scalefactors = {
        "spot_diameter_fullres": 100,
        "tissue_hires_scalef": 1.0,
        "tissue_lowres_scalef": 0.5,
    }
    (out / "spatial" / "scalefactors_json.json").write_text(
        json.dumps(scalefactors))

    # 组织背景图（squidpy read.visium load_images=True 需要 hires/lowres png；
    # hires_scalef=1.0 → 与 fullres 同尺寸，lowres 取半）
    from PIL import Image

    side = (n_side + 1) * 100
    base = rng.integers(225, 248, (side, side, 3), dtype=np.int16)
    hires = Image.fromarray(base.astype(np.uint8))
    hires.save(out / "spatial" / "tissue_hires_image.png")
    hires.resize((side // 2, side // 2)).save(
        out / "spatial" / "tissue_lowres_image.png")

    print(f"tiny visium written: {out} ({len(spots)} spots x {len(genes)} genes,"
          f" 3 domains {np.bincount(domains).tolist()})")


if __name__ == "__main__":
    main()
