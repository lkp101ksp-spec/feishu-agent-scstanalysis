"""Phase 20/31/32/33/34/35/36/37/B1：单细胞 sc_* 工具注册（spec §4；25 个 L1_compute 工具）。

BioRunner 由 runtime 组装注入（image/workspace/data_roots 可配）；
handler 捕获 BioRunError 转工具级 error_code/error_message。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.tools.bio.bio_runner import (
    BioRunError,
    BioRunner,
    compute_dataset_id,
    parse_gene_list,
)
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


def _err(exc: BioRunError) -> dict[str, str]:
    """BioRunError → 工具错误输出（ToolHandler 透传 error_code）。"""
    return {"error_code": exc.error_code, "error_message": str(exc)}


def register_l3_singlecell(
    registry: ToolRegistry,
    runner: BioRunner,
    *,
    bio_use_gpu: bool = False,
    bio_gpu_image: str = "feishu-research-agent/bio:gpu-latest",
    bio_scenic_db_root: str = "",
) -> None:
    """注册 sc_* 25 工具（runner 由 runtime 装配后传入）。

    bio_use_gpu=True 时 sc_process/sc_markers 切 GPU 镜像 + --gpus all
    （Phase 25，spec 2026-09-02-bio-gpu-image-design §1.5）。

    约定：新增工具时 handler 的 runner.run timeout_sec 必须与
    ToolSpec.timeout_sec 一致（缺失会静默回退 BioRunner 默认 900s）。
    """

    def _accel() -> tuple[str | None, bool]:
        """GPU 开关分流：开→(gpu_image, True)；关→(None=runner 默认镜像, False)。"""
        if bio_use_gpu:
            return bio_gpu_image, True
        return None, False

    def sc_load(*, path: str, format: str = "auto") -> dict[str, Any]:  # noqa: A002
        """读入本地单细胞数据 → dataset_ref + 概要统计。"""
        # 规划纠偏：模型有时把既有 dataset_ref 当文件路径塞给 sc_load
        # （ut-7 真机复现 SC_PATH_FORBIDDEN）——若 path 恰是 workspace 下
        # 已存在的数据集目录名（纯名字、无路径分隔符），直通返回该 ref，
        # 不重复加载。宽松匹配 hex 以外名字亦可，只要目录存在。
        ref_candidate = path.strip()
        ws_root = getattr(runner, "workspace_root", "")  # 单测 mock 可能无此属性
        ws = Path(ws_root) if ws_root else None
        if (ws is not None and ref_candidate and "/" not in ref_candidate
                and "\\" not in ref_candidate and ".." not in ref_candidate
                and (ws / ref_candidate).is_dir()):
            return {"dataset_ref": ref_candidate,
                    "note": "existing dataset (passthrough, skip reload)"}
        try:
            mount_root, rel, host = runner.resolve_data_path(path)
            dataset_id = compute_dataset_id(host)
            out = runner.run(
                "load",
                {"path": rel, "dataset_id": dataset_id},
                mounts=[(mount_root, "/data")],
                timeout_sec=600,
            )
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_qc(*, dataset_ref: str, min_genes: int = 600,
              min_cells: int = 3, max_mt_pct: float = 20.0) -> dict[str, Any]:
        """质控过滤 → filtered.h5ad + 前后统计。"""
        try:
            out = runner.run("qc", {
                "dataset_id": dataset_ref,
                "min_genes": min_genes, "min_cells": min_cells,
                "max_mt_pct": max_mt_pct,
            }, image=None, gpus=False, timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_process(*, dataset_ref: str, n_top_hvg: int = 2000,
                   n_pcs: int = 50, n_neighbors: int = 15,
                   resolution: float = 1.0) -> dict[str, Any]:
        """标准流程（归一化→HVG→PCA→UMAP→Leiden）→ processed.h5ad + umap.png。"""
        image, gpus = _accel()
        try:
            out = runner.run("process", {
                "dataset_id": dataset_ref,
                "n_top_hvg": n_top_hvg, "n_pcs": n_pcs,
                "n_neighbors": n_neighbors, "resolution": resolution,
            }, image=image, gpus=gpus, timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_markers(*, dataset_ref: str, method: str = "wilcoxon",
                   top_n: int = 10) -> dict[str, Any]:
        """每簇差异基因 → markers JSON + dotplot.png。"""
        image, gpus = _accel()
        try:
            out = runner.run("markers", {
                "dataset_id": dataset_ref, "method": method,
                "top_n": top_n,
            }, image=image, gpus=gpus, timeout_sec=1200)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_plot(*, dataset_ref: str, genes: list[str],
                kind: str = "violin") -> dict[str, Any]:
        """指定基因画图（violin/umap_gene）→ png 路径列表。"""
        try:
            out = runner.run("plot", {
                "dataset_id": dataset_ref,
                "genes": parse_gene_list(genes), "kind": kind,
            }, timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_enrichment(*, dataset_ref: str, group: str = "",
                      gene_sets: list[str] | None = None,
                      top_n: int = 15, min_log2fc: float = 0.25,
                      method: str = "wilcoxon") -> dict[str, Any]:
        """富集分析（Phase 31）：DEG→ORA + GSEA → csv/png。"""
        try:
            out = runner.run("enrichment", {
                "dataset_id": dataset_ref, "group": group,
                "gene_sets": gene_sets or ["hallmark", "go_bp", "kegg"],
                "top_n": top_n, "min_log2fc": min_log2fc,
                "rank_method": method,
            }, timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_score_genes(*, dataset_ref: str,
                       gene_sets: dict[str, list[str]]) -> dict[str, Any]:
        """基因集打分（Phase 32）：多基因集 score_genes → 分数/图。"""
        try:
            out = runner.run("score", {
                "dataset_id": dataset_ref, "gene_sets": gene_sets,
            }, timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_metabolism(*, dataset_ref: str, top_n: int = 30,
                      species: str = "human") -> dict[str, Any]:
        """代谢通路活性（Phase 32）：KEGG 逐通路打分 → 簇均值+热图。"""
        try:
            out = runner.run("metabolism", {
                "dataset_id": dataset_ref, "top_n": top_n,
                "species": species,
            }, timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_pseudotime(*, dataset_ref: str, root_marker: str = "") -> dict[str, Any]:
        """扩散伪时序（Phase 32）：diffmap+DPT → 伪时序/轨迹图。"""
        try:
            out = runner.run("pseudotime", {
                "dataset_id": dataset_ref, "root_marker": root_marker,
            }, timeout_sec=1200)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_de(*, dataset_ref: str, groupby: str, group_a: str,
              group_b: str, method: str = "wilcoxon", top_n: int = 20) -> dict[str, Any]:
        """组间差异（Phase 33）：两组定向 DE → csv+火山图。"""
        try:
            out = runner.run("de", {
                "dataset_id": dataset_ref, "groupby": groupby,
                "group_a": group_a, "group_b": group_b,
                "method": method, "top_n": top_n,
            }, timeout_sec=1200)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_subcluster(*, dataset_ref: str, clusters: list[str],
                      n_top_hvg: int = 2000, n_pcs: int = 50,
                      n_neighbors: int = 15,
                      resolution: float = 1.0) -> dict[str, Any]:
        """亚聚类（Phase 33）：指定簇子集重聚类 → 新 dataset_ref。"""
        try:
            out = runner.run("subcluster", {
                "dataset_id": dataset_ref, "clusters": clusters,
                "n_top_hvg": n_top_hvg, "n_pcs": n_pcs,
                "n_neighbors": n_neighbors, "resolution": resolution,
            }, timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_integrate(*, dataset_ref: str, batch: str,
                     method: str = "bbknn", n_top_hvg: int = 2000,
                     n_pcs: int = 50, n_neighbors: int = 15,
                     resolution: float = 1.0) -> dict[str, Any]:
        """批次整合（Phase 33）：bbknn → 新 dataset_ref。"""
        try:
            out = runner.run("integrate", {
                "dataset_id": dataset_ref, "batch": batch,
                "method": method, "n_top_hvg": n_top_hvg,
                "n_pcs": n_pcs, "n_neighbors": n_neighbors,
                "resolution": resolution,
            }, timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_cellfreq(*, dataset_ref: str, by: str, group: str = "",
                    celltype_col: str = "leiden") -> dict[str, Any]:
        """组成比较（Phase 33）：比例表+卡方 → csv+堆叠图。"""
        try:
            out = runner.run("cellfreq", {
                "dataset_id": dataset_ref, "by": by, "group": group,
                "celltype_col": celltype_col,
            }, timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_cellchat(*, dataset_ref: str, celltype_col: str = "leiden",
                    species: str = "human", expr_prop: float = 0.1,
                    min_cells: int = 10, top_n: int = 30,
                    max_cells_per_group: int = 0,
                    method: str = "cellchat",
                    group_col: str = "") -> dict[str, Any]:
        """细胞通讯（Phase 34/47）：liana → LR 表+dotplot+热图；
        method=rank_aggregate 五方法共识；group_col 两组差异通讯。"""
        try:
            out = runner.run("cellchat", {
                "dataset_id": dataset_ref, "celltype_col": celltype_col,
                "species": species, "expr_prop": expr_prop,
                "min_cells": min_cells, "top_n": top_n,
                "max_cells_per_group": max_cells_per_group,
                "method": method, "group_col": group_col,
            }, timeout_sec=3600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_milo(*, dataset_ref: str, sample_col: str, group_col: str,
                group_a: str, group_b: str, k: int = 0,
                top_n: int = 20, max_cells_per_sample: int = 0) -> dict[str, Any]:
        """差异丰度（Phase 34）：KNN 邻域 + NB-GLM → da csv+UMAP。"""
        try:
            out = runner.run("milo", {
                "dataset_id": dataset_ref, "sample_col": sample_col,
                "group_col": group_col, "group_a": group_a,
                "group_b": group_b, "k": k, "top_n": top_n,
                "max_cells_per_sample": max_cells_per_sample,
            }, timeout_sec=3600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_deconv(*, dataset_ref: str, bulk_file: str,
                  celltype_col: str = "leiden", method: str = "wnnls",
                  top_n: int = 200) -> dict[str, Any]:
        """bulk 解卷积（Phase 34）：wNNLS/NuSVR → 比例 csv+图。

        bulk_file 复用 sc_load 的数据根白名单校验与 /data 挂载。
        """
        try:
            mount_root, rel, host = runner.resolve_data_path(bulk_file)
            out = runner.run(
                "deconv",
                {"dataset_id": dataset_ref, "bulk_path": rel,
                 "celltype_col": celltype_col, "method": method,
                 "top_n": top_n},
                mounts=[(mount_root, "/data")],
                timeout_sec=1200,
            )
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_annotate(*, dataset_ref: str, method: str = "celltypist",
                    model: str = "Immune_All_Low.pkl",
                    marker_sets: dict[str, list[str]] | None = None,
                    celltype_col: str = "leiden",
                    out_col: str = "annotation") -> dict[str, Any]:
        """细胞注释（Phase 35）：celltypist 参考 / marker 打分双路。"""
        try:
            out = runner.run("annotate", {
                "dataset_id": dataset_ref, "method": method,
                "model": model, "marker_sets": marker_sets,
                "celltype_col": celltype_col, "out_col": out_col,
            }, timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_meta(*, dataset_ref: str, op: str, col: str = "",
                mapping: dict[str, str] | None = None, out_col: str = "",
                csv_file: str = "", key_col: str = "",
                old: str = "", new: str = "") -> dict[str, Any]:
        """元数据编辑（Phase 35）：merge_csv/map_values/rename_col。"""
        try:
            payload = {
                "dataset_id": dataset_ref, "op": op, "col": col,
                "mapping": mapping, "out_col": out_col,
                "key_col": key_col, "old": old, "new": new,
            }
            if op == "merge_csv":
                mount_root, rel, host = runner.resolve_data_path(csv_file)
                payload["csv_path"] = rel
                out = runner.run("meta", payload,
                                 mounts=[(mount_root, "/data")],
                                 timeout_sec=600)
            else:
                out = runner.run("meta", payload, timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_doublet(*, dataset_ref: str, expected_rate: float = 0.06,
                   n_prin_comps: int = 30,
                   celltype_col: str = "leiden") -> dict[str, Any]:
        """双联体检测（Phase 35）：scrublet → 写回 doublet 列。"""
        try:
            out = runner.run("doublet", {
                "dataset_id": dataset_ref, "expected_rate": expected_rate,
                "n_prin_comps": n_prin_comps, "celltype_col": celltype_col,
            }, timeout_sec=1200)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_cellcycle(*, dataset_ref: str,
                     celltype_col: str = "leiden") -> dict[str, Any]:
        """细胞周期打分（Phase 35）：Tirosh S/G2M → 写回 phase 列。"""
        try:
            out = runner.run("cellcycle", {
                "dataset_id": dataset_ref, "celltype_col": celltype_col,
            }, timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_scenic(*, dataset_ref: str, species: str = "human",
                  db: str = "500bp", max_cells: int = 3000,
                  celltype_col: str = "leiden", n_workers: int = 2,
                  seed: int = 42) -> dict[str, Any]:
        """转录调控网络（Phase 36，pySCENIC）：DB 目录只读挂载进容器。"""
        db_root = (Path(bio_scenic_db_root) if bio_scenic_db_root
                   else Path(__file__).resolve().parents[3])
        ct_dir = db_root / "cisTarget_databases"
        ma_dir = db_root / "motifAnnotations"
        if not ct_dir.is_dir() or not ma_dir.is_dir():
            return {
                "error_code": "SC_CONFIG",
                "error_message": (
                    f"SCENIC db not found: expect {ct_dir} and {ma_dir}; "
                    "set BIO_SCENIC_DB_ROOT to the directory containing "
                    "cisTarget_databases/ and motifAnnotations/"),
            }
        try:
            out = runner.run("scenic", {
                "dataset_id": dataset_ref, "species": species, "db": db,
                "max_cells": max_cells, "celltype_col": celltype_col,
                "n_workers": n_workers, "seed": seed,
            }, mounts=[(str(ct_dir), "/scenic_db/cistarget"),
                       (str(ma_dir), "/scenic_db/motifannot")],
                timeout_sec=3600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_wnn(*, rna_file: str, adt_file: str, rna_dims: int = 30,
               adt_dims: int = 18, resolution: float = 1.0,
               n_neighbors: int = 20, seed: int = 42) -> dict[str, Any]:
        """WNN 多组学整合（Phase 37，muon）：双文件 → 新 dataset_ref。"""
        try:
            mount_r, rel_r, host_r = runner.resolve_data_path(rna_file)
            mount_a, rel_a, host_a = runner.resolve_data_path(adt_file)
            dataset_id = (compute_dataset_id(host_r)[:6]
                          + compute_dataset_id(host_a)[:6])
            payload = {
                "dataset_id": dataset_id,
                "rna_path": rel_r if mount_a == mount_r else rel_r,
                "adt_path": rel_a,
                "rna_dims": rna_dims, "adt_dims": adt_dims,
                "resolution": resolution, "n_neighbors": n_neighbors,
                "seed": seed,
            }
            if mount_a == mount_r:
                out = runner.run("wnn", payload,
                                 mounts=[(mount_r, "/data")],
                                 timeout_sec=1200)
            else:
                # 异根：脚本 DATA_ROOT 指向 /data_rna，ADT 换绝对容器路径
                payload["rna_path"] = rel_r
                payload["adt_path"] = f"/data_adt/{rel_a}"
                out = runner.run("wnn", payload,
                                 mounts=[(mount_r, "/data"),
                                         (mount_a, "/data_adt")],
                                 timeout_sec=1200)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_knockout(*, dataset_ref: str, gko: str,
                    celltype_col: str = "", group: str = "",
                    n_genes: int = 1000, n_net: int = 10,
                    n_cells: int = 500, min_lib_size: int = 1000,
                    mt_threshold: float = 0.1) -> dict[str, Any]:
        """虚拟敲除（Phase 37，scTenifoldKnk R 保真链路）。"""
        try:
            out = runner.run("knockout", {
                "dataset_id": dataset_ref, "gko": gko,
                "celltype_col": celltype_col, "group": group,
                "n_genes": n_genes, "n_net": n_net, "n_cells": n_cells,
                "min_lib_size": min_lib_size, "mt_threshold": mt_threshold,
            }, timeout_sec=3600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_cnv(*, dataset_ref: str, method: str = "infercnvpy",
               celltype_col: str = "leiden",
               ref_groups: list[str] | None = None,
               resolution: float = 1.0,
               cluster_smooth: bool = False) -> dict[str, Any]:
        """CNV 推断与恶性判定（B1）：双后端 → 亚克隆。cluster_smooth
        仅作用于 cnvturbo：HMM 细胞级判定后加簇级多数投票平滑。"""
        try:
            out = runner.run("cnv", {
                "dataset_id": dataset_ref, "method": method,
                "celltype_col": celltype_col, "ref_groups": ref_groups,
                "resolution": resolution,
                "cluster_smooth": cluster_smooth,
            }, timeout_sec=3600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    registry.register(ToolSpec(
        name="sc_load",
        description=(
            "读入本地单细胞数据（.h5ad 或 10x mtx 目录）。"
            "输出 dataset_ref（下游 sc_* 工具用 <node_id>.dataset_ref 引用）、"
            "n_cells、n_genes、mt_pct 概要。path 必须在管理员允许的数据目录内。"
            "注意：若用户给的是此前分析过的数据集引用（12 位 hex，如 "
            "f1e89bf88edc），不要重新 sc_load——直接把该 ref 作为下游工具的 "
            "dataset_ref 参数；sc_load 仅用于首次读入原始数据文件。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "本地数据文件路径（如 D:/sc_data/pbmc.h5ad）"},
                "format": {"type": "string", "default": "auto",
                           "description": "h5ad 或 mtx10x（默认自动探测）"},
            },
            "required": ["path"],
        },
        risk_level="L1_compute",
        handler=sc_load,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="sc_qc",
        description=(
            "质控过滤（最小基因数/最小细胞数/最大线粒体比例%）→ filtered.h5ad。"
            "输出过滤前后细胞/基因数与剔除数。"
            "注意：小规模/测试数据每细胞（spot）基因数可能仅几十，min_genes 过大会"
            "全滤光——若失败，错误消息含 genes/cell 分布（median/p90/max），请按"
            "median 以下调低 min_genes 重试。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_load 输出的 dataset_ref"},
                "min_genes": {"type": "integer", "default": 600},
                "min_cells": {"type": "integer", "default": 3},
                "max_mt_pct": {"type": "number", "default": 20.0},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_qc,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="sc_process",
        description=(
            "标准分析流程：归一化→高变基因→PCA→UMAP→Leiden 聚类，"
            "产出 processed.h5ad 与 umap.png（每簇细胞数、cluster_sizes）。"
            "建议先跑 sc_qc（未跑时本工具用默认过滤）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "n_top_hvg": {"type": "integer", "default": 2000},
                "n_pcs": {"type": "integer", "default": 50},
                "n_neighbors": {"type": "integer", "default": 15},
                "resolution": {"type": "number", "default": 1.0},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_process,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="sc_markers",
        description=(
            "每簇差异基因（rank_genes_groups），输出每簇 top 基因列表"
            "（gene/score/log2fc）与 dotplot.png。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "method": {"type": "string", "default": "wilcoxon",
                           "enum": ["wilcoxon", "t-test"]},
                "top_n": {"type": "integer", "default": 10, "maximum": 50},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_markers,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="sc_plot",
        description=(
            "指定基因可视化（kind=violin 按簇小提琴 / umap_gene UMAP 着色），"
            "输出 png 路径列表。最多 6 个基因/次。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "genes": {"type": "array", "items": {"type": "string"},
                          "maxItems": 6,
                          "description": "基因符号列表（如 CD3D/MS4A1）"},
                "kind": {"type": "string", "default": "violin",
                         "enum": ["violin", "umap_gene"]},
            },
            "required": ["dataset_ref", "genes"],
        },
        risk_level="L1_compute",
        handler=sc_plot,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="sc_enrichment",
        description=(
            "富集分析（Phase 31，对齐 clusterProfiler ORA+GSEA 能力）："
            "指定簇 rank_genes_groups 出 DEG → 上调基因 ORA（超几何检验）"
            "+ 全基因排序 GSEA（prerank）。基因集：hallmark（50 通路）/"
            "go_bp（GO 生物过程）/ kegg（KEGG 2021 通路），可任选组合。"
            "输出每集 top 通路（ORA: adj_p/odds_ratio/genes；GSEA: nes/fdr_q）"
            "与 ora.csv/gsea.csv/柱状图 png。需先跑 sc_process。"
            "上调基因 <5 时会报错——此时调低 min_log2fc 重试。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "group": {"type": "string",
                          "description": "目标簇（leiden 标签，如 '0'；"
                                         "空=第一个簇）"},
                "gene_sets": {
                    "type": "array", "uniqueItems": True,
                    "items": {"type": "string",
                              "enum": ["hallmark", "go_bp", "kegg",
                                       "kegg_mouse", "wikipathways_mouse"]},
                    "default": ["hallmark", "go_bp", "kegg"],
                    "description": "基因集别名组合；小鼠数据用 kegg_mouse"
                                   "（KEGG_2019_Mouse）/ wikipathways_mouse"
                                   "（WikiPathways_2019_Mouse），人源三库"
                                   " hallmark/go_bp/kegg"},
                "top_n": {"type": "integer", "default": 15,
                          "maximum": 30,
                          "description": "每基因集返回/绘图的通路数"},
                "min_log2fc": {"type": "number", "default": 0.25,
                               "description": "ORA 上调基因入选阈值"},
                "method": {"type": "string", "default": "wilcoxon",
                           "enum": ["wilcoxon", "t-test"],
                           "description": "DEG 检验方法"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_enrichment,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="sc_score_genes",
        description=(
            "基因集打分（Phase 32，等价 Seurat AddModuleScore / toolsv1 基因集打分）："
            "自定义基因集（如 T 细胞毒性 {Cytotoxic: [GZMB,PRF1]}、"
            "耗竭 {Exhausted: [PDCD1,TIGIT,LAG3]}）一次多集打分，"
            "输出每集每簇 mean/median 排名、gene_set_scores.csv、"
            "UMAP 着色图与按簇小提琴图。基因与数据求交；"
            "空交集的集被跳过（全部为空时报错）。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "gene_sets": {
                    "type": "object",
                    "minProperties": 1, "maxProperties": 8,
                    "additionalProperties": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "description": "基因集字典 {集名: [基因符号列表]}，"
                                   "最多 8 个集",
                },
            },
            "required": ["dataset_ref", "gene_sets"],
        },
        risk_level="L1_compute",
        handler=sc_score_genes,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="sc_metabolism",
        description=(
            "代谢通路活性分析（Phase 32，对齐 scMetabolism）：基于 KEGG "
            "通路库（镜像内置，离线）逐通路单细胞打分，输出代谢活性全矩阵 csv、"
            "簇×通路均值 csv、簇间方差 top_n 通路热图与 top 通路 UMAP 图。"
            "比较各簇代谢重编程（糖酵解/OXPHOS 等）首选。需先跑 sc_process。"
            "物种经 species 选择：human→KEGG_2021_Human / "
            "mouse→KEGG_2019_Mouse（基因符号按数据物种对应）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "top_n": {"type": "integer", "default": 30, "maximum": 100,
                          "description": "返回/绘图的高方差通路数"},
                "species": {"type": "string", "default": "human",
                            "enum": ["human", "mouse"],
                            "description": "物种（选 KEGG 人/小鼠通路库）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_metabolism,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="sc_pseudotime",
        description=(
            "扩散伪时序（Phase 32，scanpy diffmap+DPT，对齐 Monocle 拟时序排序场景）："
            "推断细胞分化/发育顺序。root_marker 指定根细胞标记基因"
            "（取其表达最高的细胞为根，如干细胞/前体 marker；"
            "空或不存在则取第 0 个细胞）。输出每簇伪时序均值表、"
            "pseudotime.csv、UMAP 伪时序图（标注根细胞）与 PAGA 轨迹图。"
            "注意：不推断分支（Monocle2 BEAM/CytoTRACE2 不在范围）。"
            "需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "root_marker": {"type": "string", "default": "",
                                "description": "根细胞定位标记基因"
                                               "（如 NKG7/干细胞 marker）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_pseudotime,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="sc_de",
        description=(
            "组间差异分析（Phase 33，对齐 server_differential_analysis）："
            "obs 任意分组列上的两组定向对比（如 condition 下 treated vs "
            "control、样本 A vs B、同一细胞类型内用药前后）。输出上调/下调"
            "top 基因表、全量 DEG csv 与火山图 png。列或取值不存在时错误"
            "消息会列出可用分组列与已有取值。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "groupby": {"type": "string",
                            "description": "obs 分组列名（如 condition/sample）"},
                "group_a": {"type": "string",
                            "description": "对比组（上调方向）取值"},
                "group_b": {"type": "string",
                            "description": "参照组取值"},
                "method": {"type": "string", "default": "wilcoxon",
                           "enum": ["wilcoxon", "t-test"]},
                "top_n": {"type": "integer", "default": 20,
                          "maximum": 100,
                          "description": "上调/下调各返回的基因数"},
            },
            "required": ["dataset_ref", "groupby", "group_a", "group_b"],
        },
        risk_level="L1_compute",
        handler=sc_de,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="sc_subcluster",
        description=(
            "亚聚类（Phase 33）：取指定 leiden 簇子集，从 raw 重跑 HVG→PCA→"
            "UMAP→Leiden（标签重编 0..k），发现大群内部异质性。输出**新 "
            "dataset_ref**（形如 {原id}_sub0-1）——后续 sc_markers/sc_enrichment/"
            "sc_score_genes 等工具直接传该新 ref 即可，支持多级亚聚类。"
            "产物含新 umap.png 与簇大小。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "父级 sc_process 的 dataset_ref"},
                "clusters": {
                    "type": "array", "minItems": 1,
                    "items": {"type": "string"},
                    "description": "要亚聚类的 leiden 簇标签列表，如 ['0','1']"},
                "n_top_hvg": {"type": "integer", "default": 2000},
                "n_pcs": {"type": "integer", "default": 50},
                "n_neighbors": {"type": "integer", "default": 15},
                "resolution": {"type": "number", "default": 1.0,
                               "description": "子集内 Leiden 分辨率"},
            },
            "required": ["dataset_ref", "clusters"],
        },
        risk_level="L1_compute",
        handler=sc_subcluster,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="sc_integrate",
        description=(
            "批次整合（Phase 33，对齐 server_batch_correction）：多样本合并"
            "去除批次效应（bbknn）。输入与 sc_process 相同（filtered/raw），"
            "obs 需含批次列（如 sample/batch）。输出**新 dataset_ref**"
            "（形如 {原id}_bbknn）与双联 UMAP 图（左按批次着色看混合程度、"
            "右按新 leiden）——下游工具直接传新 ref。批次列不存在时错误消息"
            "列出可用列。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_load/sc_qc 输出的 dataset_ref"},
                "batch": {"type": "string",
                          "description": "obs 批次列名（如 sample/batch）"},
                "method": {"type": "string", "default": "bbknn",
                           "enum": ["bbknn"]},
                "n_top_hvg": {"type": "integer", "default": 2000},
                "n_pcs": {"type": "integer", "default": 50},
                "n_neighbors": {"type": "integer", "default": 15},
                "resolution": {"type": "number", "default": 1.0},
            },
            "required": ["dataset_ref", "batch"],
        },
        risk_level="L1_compute",
        handler=sc_integrate,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="sc_cellfreq",
        description=(
            "细胞组成比较（Phase 33，对齐 server_cell_freq_merged）：按样本/"
            "受试者列统计各簇细胞比例（csv + 堆叠柱状图）；给出分组列时"
            "每簇做卡方检验（该簇 vs 其余 × 分组，返回原始 p 未做多重校正）。"
            "回答\"哪种细胞在病例组富集/缺失\"类问题。需先跑 sc_process，"
            "obs 需含样本列（如 sample/orig.ident）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "by": {"type": "string",
                       "description": "统计单元列（样本/受试者，如 sample）"},
                "group": {"type": "string", "default": "",
                          "description": "比较分组列（如 condition）；"
                                         "空则只出比例表"},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "细胞标签列（leiden 或注释列）"},
            },
            "required": ["dataset_ref", "by"],
        },
        risk_level="L1_compute",
        handler=sc_cellfreq,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="sc_cellchat",
        description=(
            "细胞通讯分析（Phase 34/47）：推断细胞类型间的配体-受体互作"
            "（liana + 内置 consensus 资源库）。method=cellchat 单方法"
            "（默认）或 rank_aggregate 五方法共识排序（假阳性更低）；"
            "group_col 指定恰两取值的分组列时做两组差异通讯（各组独立"
            "推断 + 差分 LR 表与红蓝差分热图）。输出显著 LR 对 top 表、"
            "全量 csv、top LR dotplot 与细胞类型间互作计数热图。回答"
            "\"哪类细胞在给谁发信号\"类问题。需先跑 sc_process。列名"
            "错误时错误消息会列出可用列。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "细胞标签列（leiden 或注释列）"},
                "species": {"type": "string", "default": "human",
                            "enum": ["human", "mouse"]},
                "expr_prop": {"type": "number", "default": 0.1,
                              "description": "细胞类型中表达比例阈值"},
                "min_cells": {"type": "integer", "default": 10,
                              "description": "细胞类型最少细胞数（低于剔除）"},
                "top_n": {"type": "integer", "default": 30, "maximum": 100},
                "max_cells_per_group": {
                    "type": "integer", "default": 0,
                    "description": "每组（细胞类型）抽样上限，0=全量。"
                                   "大数据集可设 100 显著加速，"
                                   "结果为抽样估计"},
                "method": {"type": "string", "default": "cellchat",
                           "enum": ["cellchat", "rank_aggregate"],
                           "description": "cellchat 单方法（默认）或 "
                                          "rank_aggregate 五方法共识"},
                "group_col": {"type": "string", "default": "",
                              "description": "分组列（如 group）：非空且"
                                             "恰两取值时做两组差异通讯；"
                                             "留空=单组"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_cellchat,
        timeout_sec=3600,
    ))
    registry.register(ToolSpec(
        name="sc_milo",
        description=(
            "差异丰度分析（Phase 34，Python 复刻 miloR 思路）：在 KNN 图"
            "邻域上检验\"哪些细胞状态在 A 组比 B 组显著增多/减少\""
            "（如病灶富集的细胞亚群）。逐邻域 NB-GLM + BH 校正，输出"
            "da csv 与 UMAP 着色图（FDR<0.1 邻域黑边高亮）。"
            "需先跑 sc_process；obs 需含样本列与分组列，且每组样本数≥2。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "sample_col": {"type": "string",
                               "description": "样本/受试者列（如 sample）"},
                "group_col": {"type": "string",
                              "description": "分组列（如 condition）"},
                "group_a": {"type": "string",
                            "description": "对比组（log2FC>0 方向）取值"},
                "group_b": {"type": "string", "description": "参照组取值"},
                "k": {"type": "integer", "default": 0,
                      "description": "邻域大小；0=自动 clip(0.1×最小样本量,"
                                     "10,50)"},
                "top_n": {"type": "integer", "default": 20, "maximum": 100},
                "max_cells_per_sample": {
                    "type": "integer", "default": 0,
                    "description": "每样本抽样上限，0=全量。"
                                   "多样本大数据集可设 100-500 加速"},
            },
            "required": ["dataset_ref", "sample_col", "group_col",
                         "group_a", "group_b"],
        },
        risk_level="L1_compute",
        handler=sc_milo,
        timeout_sec=3600,
    ))
    registry.register(ToolSpec(
        name="sc_deconv",
        description=(
            "bulk 解卷积（Phase 34，对齐 server_bulk_deconvolution）："
            "以当前单细胞数据为参考，估计 bulk 表达矩阵中各细胞类型比例"
            "（method=wnnls MuSiC 式加权 NNLS / nusvr CIBERSORT 式线性"
            "SVR）。bulk_file 为数据目录内 csv/tsv（行=基因列=样本，"
            "方向放反会自动转置）。输出比例 csv、signature csv、堆叠柱状"
            "图与热图。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc 参考的 dataset_ref"},
                "bulk_file": {"type": "string",
                              "description": "bulk 表达矩阵本地路径（必须在"
                                             "管理员允许的数据目录内）"},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "参考细胞标签列"},
                "method": {"type": "string", "default": "wnnls",
                           "enum": ["wnnls", "nusvr"]},
                "top_n": {"type": "integer", "default": 200, "maximum": 2000,
                          "description": "signature 基因数"},
            },
            "required": ["dataset_ref", "bulk_file"],
        },
        risk_level="L1_compute",
        handler=sc_deconv,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="sc_annotate",
        description=(
            "细胞类型注释（Phase 35，对齐 server_cell_annotation）："
            "method=celltypist 用参考模型自动注释（写回 celltypist_label/"
            "celltypist_conf，model 不存在时错误列出可用模型）；"
            "method=markers 用用户 marker 基因集打分按簇投票（写回 "
            "out_col 指定列）。注释列写回 processed.h5ad——sc_plot/"
            "sc_cellfreq/sc_cellchat 的 celltype_col 可直接引用。"
            "需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "method": {"type": "string", "default": "celltypist",
                           "enum": ["celltypist", "markers"]},
                "model": {"type": "string", "default": "Immune_All_Low.pkl",
                          "description": "celltypist 模型文件名（如 "
                                         "Immune_All_Low/High.pkl）"},
                "marker_sets": {
                    "type": "object",
                    "additionalProperties": {"type": "array",
                                             "items": {"type": "string"}},
                    "description": "markers 路必填：{细胞类型: [基因,...]}，"
                                   "≤20 集"},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "簇标签列（投票/平滑用）"},
                "out_col": {"type": "string", "default": "annotation",
                            "description": "markers 路写回的列名"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_annotate,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="sc_meta",
        description=(
            "元数据编辑（Phase 35，对齐 server_meta_settings）：对话式修改"
            "细胞注释表。op=merge_csv 把样本注释表（数据目录内 csv，首列"
            "=连接键）按 key_col 并入 obs（典型：给样本补 condition 分组"
            "列）；op=map_values 取值映射/合并（如 leiden 簇号改细胞类型"
            "名）；op=rename_col 列改名。结果写回 processed.h5ad。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "op": {"type": "string",
                       "enum": ["merge_csv", "map_values", "rename_col"]},
                "col": {"type": "string", "default": "",
                        "description": "map_values 的源列"},
                "mapping": {"type": "object",
                            "additionalProperties": {"type": "string"},
                            "description": "map_values 的 {旧值: 新值}"},
                "out_col": {"type": "string", "default": "",
                            "description": "map_values 输出列（缺省 "
                                           "{col}_mapped）"},
                "csv_file": {"type": "string", "default": "",
                             "description": "merge_csv 的 csv 本地路径"
                                            "（须在数据目录内）"},
                "key_col": {"type": "string", "default": "",
                            "description": "merge_csv 的 obs 连接列"},
                "old": {"type": "string", "default": "",
                        "description": "rename_col 的原列名"},
                "new": {"type": "string", "default": "",
                        "description": "rename_col 的新列名"},
            },
            "required": ["dataset_ref", "op"],
        },
        risk_level="L1_compute",
        handler=sc_meta,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="sc_doublet",
        description=(
            "双联体检测（Phase 35，scrublet）：识别两个细胞被包进同一"
            "液滴形成的假细胞。在 QC 后 counts 上打分，写回 doublet_score/"
            "predicted_doublet 到 processed.h5ad（只标记不删除）。输出"
            "各簇双联体率 csv 与 UMAP（预测双联体红圈）。expected_rate "
            "默认 0.06（10x 典型值，按上机细胞量调整）。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "expected_rate": {"type": "number", "default": 0.06,
                                  "description": "预期双联体率"},
                "n_prin_comps": {"type": "integer", "default": 30},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "簇率统计用标签列"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_doublet,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="sc_cellcycle",
        description=(
            "细胞周期打分（Phase 35）：Tirosh S/G2M 基因集（人源，内嵌"
            "离线）给每细胞定 G1/S/G2M 期，写回 S_score/G2M_score/phase "
            "到 processed.h5ad，输出 phase UMAP 与 phase×簇计数 csv。"
            "回答\"增殖活性差异/周期是否干扰聚类\"类问题。需先跑 "
            "sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "celltype_col": {"type": "string", "default": "leiden"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_cellcycle,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="sc_scenic",
        description=(
            "转录调控网络推断（Phase 36，pySCENIC 三幕）：GRNBoost2 共表达"
            "→ cisTarget motif 剪枝 → AUCell 打分，回答\"各细胞类型的核心"
            "转录因子/regulon 是什么、活性多高\"。species 选 human/mouse，"
            "db 选 500bp（快）/10kb（更多 regulon）/both。默认按簇分层抽样"
            " 3000 细胞（大计算量护栏）。产物：adjacencies 共表达表、"
            "regulons 列表、regulon_auc.csv（抽样细胞×regulon 活性）、"
            "rss.csv（簇特异 regulon 排序）、热图 png。n_regulons=0 不算"
            "失败（小样本/motif 命中低正常，note 会说明）。需先跑 "
            "sc_process。耗时较长（分钟~小时级）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "species": {"type": "string", "default": "human",
                            "enum": ["human", "mouse"]},
                "db": {"type": "string", "default": "500bp",
                       "enum": ["500bp", "10kb", "both"],
                       "description": "cisTarget rankings 库（500bp 快，"
                                      "10kb 捕获更多 regulon）"},
                "max_cells": {"type": "integer", "default": 3000,
                              "description": "分层抽样上限（GRNBoost2 是"
                                             "计算重头）"},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "簇标签列（抽样/RSS 用）"},
                "n_workers": {"type": "integer", "default": 2,
                              "description": "dask worker 数（内存随 worker "
                                             "线性涨，16g 容器勿超 4）"},
                "seed": {"type": "integer", "default": 42},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_scenic,
        timeout_sec=3600,
    ))
    registry.register(ToolSpec(
        name="sc_wnn",
        description=(
            "WNN 多组学整合（Phase 37，muon，等价 Seurat "
            "FindMultiModalNeighbors）：CITE-seq 场景把 RNA 与蛋白（ADT）"
            "两个模态加权整合成联合邻居图 → 联合 UMAP + leiden 聚类 + "
            "每细胞模态权重。输入两个 h5ad 文件（RNA counts + ADT counts，"
            "按细胞名交集对齐），输出**新的 dataset_ref**（raw=RNA "
            "lognorm，可直接接 sc_plot/sc_score/sc_annotate/sc_markers "
            "下游）。两文件须在数据目录白名单内。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "rna_file": {"type": "string",
                             "description": "RNA h5ad 本地路径（counts）"},
                "adt_file": {"type": "string",
                             "description": "ADT h5ad 本地路径（蛋白 counts）"},
                "rna_dims": {"type": "integer", "default": 30,
                             "description": "RNA PCA 维数"},
                "adt_dims": {"type": "integer", "default": 18,
                             "description": "ADT PCA 维数"},
                "resolution": {"type": "number", "default": 1.0},
                "n_neighbors": {"type": "integer", "default": 20},
                "seed": {"type": "integer", "default": 42},
            },
            "required": ["rna_file", "adt_file"],
        },
        risk_level="L1_compute",
        handler=sc_wnn,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="sc_knockout",
        description=(
            "虚拟敲除（Phase 37，scTenifoldKnk R 保真链路）：不敲真基因，"
            "在网络上模拟敲掉某个转录因子，输出全基因组差异调控排序"
            "（dRegulation Z/FC/p/padj + 火山图），回答\"敲掉 X 会影响哪些"
            "基因\"。gKO 为高变基因内的基因名；可用 celltype_col+group 只"
            "在指定细胞类型内做（≥100 细胞）。耗时分钟~小时级（n_net 次"
            "网络构建）。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "gko": {"type": "string",
                        "description": "要虚拟敲除的基因（须在 HVG 内）"},
                "celltype_col": {"type": "string", "default": "",
                                 "description": "子集用标签列（可空）"},
                "group": {"type": "string", "default": "",
                          "description": "子集用标签值（可空=全数据）"},
                "n_genes": {"type": "integer", "default": 1000,
                            "description": "HVG 数（网络规模，越大越慢）"},
                "n_net": {"type": "integer", "default": 10,
                          "description": "子抽样网络数（耗时线性）"},
                "n_cells": {"type": "integer", "default": 500,
                            "description": "每网抽细胞数"},
                "min_lib_size": {"type": "integer", "default": 1000,
                                 "description": "R 侧 QC 最小文库大小"},
                "mt_threshold": {"type": "number", "default": 0.1},
            },
            "required": ["dataset_ref", "gko"],
        },
        risk_level="L1_compute",
        handler=sc_knockout,
        timeout_sec=3600,
    ))
    registry.register(ToolSpec(
        name="sc_cnv",
        description=(
            "CNV 推断与恶性判定（B1，inferCNV 式有参考模式）：以免疫/"
            "基质等非恶性细胞为基线推断全基因组拷贝数变异，输出每细胞"
            " cnv_score、恶性判定 is_malignant 与恶性亚克隆 cnv_subclone"
            "（写回 processed.h5ad），附染色体热图与注释类型×恶性计数表。"
            "method 默认 infercnvpy（成熟参考实现），可选 cnvturbo（对齐"
            " R inferCNV HMM i6，可交叉验证）。参考细胞默认从 "
            "celltype_col 按内置非恶性清单子串匹配（T/B/NK/Macrophage/"
            "Monocyte/Dendritic/Neutrophil/Fibroblast/Endothelial/"
            "Pericyte/Smooth muscle/Erythrocyte），也可 ref_groups 显式"
            "指定（如含正常上皮时传 ['T cells', 'Epithelial']）；零匹配"
            "报错（不静默降级）。需先 sc_process；人源 GRCh38 基因符号。"
            "结果列可作 sc_plot/sc_de 分组。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "method": {"type": "string", "default": "infercnvpy",
                           "enum": ["infercnvpy", "cnvturbo"]},
                "celltype_col": {"type": "string", "default": "leiden",
                                 "description": "参考细胞来源列"
                                                "（leiden 或注释列）"},
                "ref_groups": {
                    "type": "array", "items": {"type": "string"},
                    "description": "显式参考细胞类型列表（覆盖默认清单）"},
                "resolution": {"type": "number", "default": 1.0,
                               "description": "恶性亚克隆 leiden 分辨率"},
                "cluster_smooth": {
                    "type": "boolean", "default": False,
                    "description": "仅 cnvturbo：HMM 细胞级判定后加 CNV 簇"
                                   "级多数投票平滑（与 infercnvpy 后处理"
                                   "对齐；口径评估 2026-09-12）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=sc_cnv,
        timeout_sec=3600,
    ))
