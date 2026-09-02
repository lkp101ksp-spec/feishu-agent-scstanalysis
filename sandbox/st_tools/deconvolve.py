"""st_deconvolve：cell2location 细胞类型反卷积 → 每 spot 类型丰度 + 空间图。

stdin: {"dataset_id": "<st 数据集>", "sc_ref_dataset": "<sc 12hex>" 或
        "sc_ref_path": "/data/相对路径.h5ad", "ref_label_col": "",
        "max_epochs": 30000, "n_cells_per_location": 8, "detection_alpha": 20}
双参考来源（spec §2.4）：workspace 内 sc 产物（filtered counts + processed
leiden 按 barcode 对齐）或 /data 挂载的独立 h5ad（注释列可指定/自动探测）。
参考与空间均用原始 counts；官方建议剔除 MT 基因 + 宽松基因过滤。
产出：deconv.h5ad + 每细胞类型空间着色图（pngs）+ 丰度摘要。

cell2location 0.1.5 API 实测修正（Task 1 容器内探测结论）：
- 参考签名在 ref.varm["means_per_cluster_mu_fg"]（n_vars × n_factors），
  旧教程的 ref.var["means_est_inf_*"] 列已不存在；列名换成因子名后
  直接作 cell_state_df（要求 (n_vars, n_factors) 不转置，且 index ==
  adata.var_names——varm DataFrame 天然满足）。
- 空间侧 sp.obsm["q05_cell_abundance_w_sf"] 列名带
  "q05cell_abundance_w_sf_" 前缀，写 obs/画图/摘要前 rename 纯因子名。
- train 与 export_posterior sample_kwargs 均用 accelerator="cpu"。

fail() 后 raise SystemExit(1)：批① fail() 模式（bio_runner 侧
"stdout JSON + exit 1" 契约），错误路径 rc=1 供 BioRunner 透传。
"""
from __future__ import annotations

from common import emit, run


def _load_ref(args: dict):
    """双来源读取参考：返回 (counts AnnData, label 来源描述)。

    场景 A（sc_ref_dataset=12hex）：workspace 内 sc 产物——counts 取
    filtered/raw h5ad，注释取 processed.h5ad 的 obs["leiden"]（sc_process
    固定输出列名），按 barcode 对齐（<50% 交集视为错配）；缺 processed
    → ST_REF_INVALID 提示先跑 sc_process。
    场景 B（sc_ref_path）：/data 白名单挂载 h5ad；X 含负值（scaled）时
    回退 adata.raw，无 raw → ST_REF_INVALID；注释列 ref_label_col 可
    指定，留空按候选清单自动探测，找不到 → ST_REF_INVALID 附 obs 列清单。
    统一产出 obs["c2l_label"]。
    """
    import anndata as ad
    from common import DATA_ROOT, WS_ROOT, fail

    label_col = str(args.get("ref_label_col") or "").strip()
    if args.get("sc_ref_dataset"):
        ds = WS_ROOT / args["sc_ref_dataset"]
        proc = ds / "processed.h5ad"
        if not proc.exists():
            fail("ST_REF_INVALID",
                 f"sc ref {args['sc_ref_dataset']} has no processed.h5ad; "
                 "run sc_process first (deconvolve uses its leiden labels)")
            raise SystemExit(1)
        counts_p = next((ds / n for n in ("filtered.h5ad", "raw.h5ad")
                         if (ds / n).exists()), None)
        if counts_p is None:
            fail("ST_REF_INVALID",
                 f"sc ref {args['sc_ref_dataset']} has no filtered/raw h5ad")
            raise SystemExit(1)
        ref = ad.read_h5ad(counts_p)
        proc_obs = ad.read_h5ad(proc, backed="r").obs
        common = ref.obs_names.intersection(proc_obs.index)
        if len(common) < ref.n_obs * 0.5:
            fail("ST_REF_INVALID",
                 "sc ref barcodes mismatch between counts and processed")
            raise SystemExit(1)
        label = proc_obs.loc[common, "leiden"].astype(str)
        ref = ref[common].copy()
        ref.obs["c2l_label"] = label.values
        return ref, "workspace sc dataset"
    ref = ad.read_h5ad(DATA_ROOT / args["sc_ref_path"])
    if ref.X is not None and (ref.X.min() < 0 if ref.n_vars else False):
        if ref.raw is None:
            fail("ST_REF_INVALID",
                 "ref X contains negatives (scaled?) and no raw counts")
            raise SystemExit(1)
        ref = ref.raw.to_adata()
    cands = ([label_col] if label_col else []) + [
        "celltype", "cell_type", "CellType", "leiden", "cluster"]
    for c in cands:
        if c in ref.obs.columns and ref.obs[c].nunique() >= 2:
            ref.obs["c2l_label"] = ref.obs[c].astype(str).values
            return ref, c
    fail("ST_REF_INVALID",
         f"ref h5ad has no usable label column; obs columns: "
         f"{list(ref.obs.columns)[:20]}; pass ref_label_col")
    raise SystemExit(1)


def _strip_mt(adata) -> None:
    """剔除 MT- 基因并把其计数快照存 obsm["MT"]（官方建议存档）。"""
    import numpy as np

    mt = adata.var_names.str.upper().str.startswith("MT-")
    if mt.any():
        x = adata[:, mt.values].X
        x = x.toarray() if hasattr(x, "toarray") else np.asarray(x)
        adata.obsm["MT"] = np.asarray(x, dtype="float32")
    adata._inplace_subset_var(~mt.values)


def main() -> None:
    """主流程：双参考读取 → 基因过滤 → NB 回归签名 → 空间映射 → 图+落盘。"""
    from common import WS_ROOT, ensure_spatial, fail, read_args

    args = read_args()
    max_epochs = int(args.get("max_epochs", 30000))
    n_cells = float(args.get("n_cells_per_location", 8))
    d_alpha = float(args.get("detection_alpha", 20))

    import anndata as ad
    sp = ad.read_h5ad(WS_ROOT / args["dataset_id"] / "filtered.h5ad")
    ensure_spatial(sp)
    ref, label_src = _load_ref(args)

    import matplotlib.pyplot as plt
    import squidpy as sq
    from cell2location.models import Cell2location, RegressionModel
    from cell2location.utils.filtering import filter_genes

    n_types = ref.obs["c2l_label"].nunique()
    if not 2 <= n_types <= 30:
        fail("ST_REF_INVALID",
             f"ref label column has {n_types} types (expect 2~30)")
        raise SystemExit(1)

    # 基因交集 + 剔 MT（官方建议）+ 宽松基因过滤
    sp = sp[:, sp.var_names.isin(ref.var_names)].copy()
    ref = ref[:, ref.var_names.isin(sp.var_names)].copy()
    _strip_mt(sp)
    _strip_mt(ref)
    sel = filter_genes(ref, cell_count_cutoff=5, cell_percentage_cutoff2=0.03,
                       nonz_mean_cutoff=1.12)
    shared = ref.var_names[sel].intersection(sp.var_names)
    if len(shared) < 20:
        fail("ST_REF_INVALID",
             f"only {len(shared)} shared genes after filtering")
        raise SystemExit(1)
    ref = ref[:, shared].copy()
    sp = sp[:, shared].copy()

    # ① 参考签名（NB 回归）：估计每因子（细胞类型）的平均表达谱
    RegressionModel.setup_anndata(
        adata=ref, batch_key=None, labels_key="c2l_label")
    mod_r = RegressionModel(ref)
    mod_r.train(max_epochs=250, batch_size=2500, accelerator="cpu")
    ref = mod_r.export_posterior(
        ref, sample_kwargs={"num_samples": 1000, "batch_size": 2500,
                            "accelerator": "cpu"})
    # 0.1.5 实测：签名矩阵在 varm（n_vars × n_factors，index 即 var_names）
    # 而非旧教程的 var["means_est_inf_*"] 列；换列名为因子名后直接作
    # cell_state_df（不转置，varm 的 index == var_names 天然通过校验）
    inf_aver = ref.varm["means_per_cluster_mu_fg"].copy()
    inf_aver.columns = ref.uns["mod"]["factor_names"]

    # ② 空间映射：N_cells_per_location/detection_alpha 为 Visium 官方推荐起点
    Cell2location.setup_anndata(adata=sp)
    mod_s = Cell2location(
        sp, cell_state_df=inf_aver,
        N_cells_per_location=n_cells, detection_alpha=d_alpha)
    mod_s.train(max_epochs=max_epochs, batch_size=None, accelerator="cpu")
    sp = mod_s.export_posterior(
        sp, sample_kwargs={"num_samples": 1000, "batch_size": sp.n_obs,
                           "accelerator": "cpu"})

    # 0.1.5 实测：丰度 obsm 列名带 "q05cell_abundance_w_sf_" 前缀，rename
    # 纯因子名后再写 obs/画图/摘要（deconv.h5ad 内保留原始 obsm 键）
    abund = sp.obsm["q05_cell_abundance_w_sf"].copy()
    abund.columns = [str(c).replace("q05cell_abundance_w_sf_", "")
                     for c in abund.columns]
    cell_types = list(abund.columns)
    sp.obs[cell_types] = abund

    ds_dir = WS_ROOT / args["dataset_id"]
    pngs = []
    top_types = abund.mean(axis=0).sort_values(ascending=False).index[:6]
    for ct in top_types:
        ax = sq.pl.spatial_scatter(sp, color=ct, return_ax=True, cmap="magma")
        ax.figure.savefig(ds_dir / f"deconv_{ct}.png", dpi=150,
                          bbox_inches="tight")
        plt.close("all")
        pngs.append(f"/ws/{args['dataset_id']}/deconv_{ct}.png")

    sp.write_h5ad(ds_dir / "deconv.h5ad")

    emit({
        "ok": True,
        "dataset_ref": args["dataset_id"],
        "ref_source": label_src,
        "n_cell_types": int(n_types),
        "cell_types": cell_types,
        "mean_abundance": {ct: round(float(abund[ct].mean()), 3)
                           for ct in cell_types},
        "pngs": pngs,
    })


if __name__ == "__main__":
    run(main)
