"""st_commot：配体受体空间通讯（COMMOT + CellChat 库）→ 图 + top LR 对。

stdin: {"dataset_id": "...", "species": "human"|"mouse", "dis_thr": 200}
- 表达取 process 落的 adata.raw 快照（normalized log 非负；scale 后的
  processed.X 含负值不可用）
- dis_thr 单位与 obsm["spatial"] 坐标一致（visium fullres 像素，
  spot 中心距 100 → 默认 200 = 2 spot 距离，spec §2.3）
- 无匹配 LR 对 → fail ST_COMMOT_EMPTY（附物种/基因命名提示）
产出：top 通路 sender/receiver 方向图 + domain×pathway 热图（走 pngs）。

fail() 后 raise SystemExit(1)：批① fail() 模式（bio_runner 侧
"stdout JSON + exit 1" 契约），错误路径 rc=1 供 BioRunner 透传。
"""
from __future__ import annotations

from common import emit, run


def main() -> None:
    from common import WS_ROOT, ensure_spatial, fail, read_args

    args = read_args()
    species = str(args.get("species", "human")).lower()
    dis_thr = float(args.get("dis_thr", 200))
    if species not in ("human", "mouse"):
        fail("INVALID_INPUT", f"species must be human|mouse, got {species!r}")
        raise SystemExit(1)

    import anndata as ad

    adata = ad.read_h5ad(WS_ROOT / args["dataset_id"] / "processed.h5ad")
    ensure_spatial(adata)
    if adata.raw is None:
        fail("ST_STATE_INVALID",
             "processed.h5ad has no raw snapshot; rerun st_process first")
        raise SystemExit(1)
    # raw 快照 + 空间信息重建 COMMOT 输入（raw.to_adata 不带 obsm）
    expr = adata.raw.to_adata()
    expr.obsm["spatial"] = adata.obsm["spatial"].copy()
    expr.uns["spatial"] = adata.uns.get("spatial", {})
    if "spatial_domain" in adata.obs:
        expr.obs["spatial_domain"] = \
            adata.obs["spatial_domain"].astype(str).values

    # 脚本名与 commot 包同名（BioRunner 约定 /opt/st_tools/commot.py）：
    # python /opt/st_tools/commot.py 时 sys.path[0]=/opt/st_tools，
    # `import commot` 会解析到脚本自身（首次容器验证踩坑：报
    # AttributeError "no attribute 'pp'"——导入的是只含 emit/run/main
    # 的脚本副本）——导入前剔除脚本目录，落到 site-packages 真 commot 包
    import sys as _sys
    from pathlib import Path as _Path
    _here = str(_Path(__file__).resolve().parent)
    _sys.path = [p for p in _sys.path if p not in ("", ".", _here)]
    # commot 0.0.3：__init__ 不暴露子模块，需显式导入绑定
    # ct.pp/ct.tl/ct.pl（实为 preprocessing/tools/plotting）
    import commot as ct
    import commot.pl  # noqa: F401 —— 绑定 ct.pl（画图 API）
    import commot.pp  # noqa: F401 —— 绑定 ct.pp（LR 库 + 过滤）
    import commot.tl  # noqa: F401 —— 绑定 ct.tl（通讯计算）
    import matplotlib.pyplot as plt
    import numpy as np

    df = ct.pp.ligand_receptor_database(
        database="CellChat", species=species, signaling_type=None)
    df_f = ct.pp.filter_lr_database(df, expr, min_cell_pct=0.05)
    if len(df_f) == 0:
        fail("ST_COMMOT_EMPTY",
             f"no CellChat LR pair matched (species={species}); check gene "
             "naming (human uppercase symbols, e.g. VEGFA/KDR) and "
             f"expression level; dis_thr={dis_thr}")
        raise SystemExit(1)

    ct.tl.spatial_communication(
        expr, database_name="cellchat", df_ligrec=df_f,
        dis_thr=dis_thr, heteromeric=True, pathway_sum=True)

    # top LR 对（transport 矩阵总量）与 top pathway（sender 总量）
    scores = []
    for key in list(expr.obsp.keys()):
        if not key.startswith("commot-cellchat-") or key.count("-") < 3:
            continue
        lr = key[len("commot-cellchat-"):]
        lig, _, rec = lr.rpartition("-")
        scores.append((lig, rec, float(expr.obsp[key].sum())))
    scores.sort(key=lambda t: -t[2])
    top_lr = [{"ligand": l, "receptor": r, "score": round(s, 4)}
              for l, r, s in scores[:10]]
    df_sender = expr.uns["commot-cellchat-sum-sender"]
    top_path = str(df_sender.sum(axis=0).idxmax())

    ds_dir = WS_ROOT / args["dataset_id"]
    pngs = []
    # top 通路方向图（sender / receiver）
    ct.tl.communication_direction(
        expr, database_name="cellchat", pathway_name=top_path, k=5)
    for summary in ("sender", "receiver"):
        ct.pl.plot_cell_communication(
            expr, database_name="cellchat", pathway_name=top_path,
            plot_method="grid", summary=summary, background="summary",
            clustering="spatial_domain" if "spatial_domain" in expr.obs
            else None,
            ndsize=8, grid_density=0.4, scale=0.00003,
            normalize_v=True, normalize_v_quantile=0.995)
        p = ds_dir / f"commot_{top_path}_{summary}.png"
        plt.savefig(p, dpi=150, bbox_inches="tight")
        plt.close("all")
        pngs.append(f"/ws/{args['dataset_id']}/{p.name}")

    # domain × pathway 平均通讯强度热图（sender/receiver）
    if "spatial_domain" in expr.obs:
        grp = expr.obs["spatial_domain"].values
        top_paths = df_sender.sum(axis=0).sort_values(
            ascending=False).index[:12]
        send = df_sender.groupby(grp).mean()[top_paths]
        recv = expr.uns["commot-cellchat-sum-receiver"].groupby(
            grp).mean()[top_paths]
        mat = np.vstack([send.values, recv.values])
        fig, ax = plt.subplots(figsize=(1 + 0.5 * len(top_paths), 4))
        im = ax.imshow(mat, aspect="auto", cmap="viridis")
        ax.set_yticks(range(mat.shape[0]))
        ax.set_yticklabels(
            [f"{d}-send" for d in send.index]
            + [f"{d}-recv" for d in recv.index], fontsize=8)
        ax.set_xticks(range(len(top_paths)))
        ax.set_xticklabels(top_paths, rotation=45, ha="right", fontsize=8)
        fig.colorbar(im, shrink=0.7)
        fig.tight_layout()
        fig.savefig(ds_dir / "commot_heatmap.png", dpi=150)
        plt.close(fig)
        pngs.append(f"/ws/{args['dataset_id']}/commot_heatmap.png")

    expr.write_h5ad(ds_dir / "commot.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "species": species,
        "dis_thr": dis_thr,
        "n_lr_pairs": int(len(df_f)),
        "top_pathway": top_path,
        "top_lr_pairs": top_lr,
        "pngs": pngs,
    })


if __name__ == "__main__":
    run(main)
