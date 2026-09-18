"""Phase 21：空间转录组 st_* 工具注册（spec §2.2，7 个 L1_compute 工具）。

与 l3_singlecell 同模式：runner 由 runtime 组装注入；st 脚本走 st 镜像
（run 时覆盖 image/script_dir）；handler 捕获 BioRunError 转错误输出。

纪律：handler 的 runner.run timeout_sec 必须与 ToolSpec.timeout_sec 一致
（缺失会静默回退 BioRunner 默认 900s，比声明的 1800s 提前杀容器——
st_process/markers/domains/commot 曾中招）；tests/unit/
test_l3_dispatch_contract.py 全工具合同测试钉死该一致性。
"""
from __future__ import annotations

from typing import Any

from orchestrator.tools.bio.bio_runner import (
    BioRunError,
    BioRunner,
    compute_dataset_id,
    compute_dataset_id_dir,
    parse_gene_list,
)
from orchestrator.tools.bio.dataset_profile import resolve_species
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec

_ST_SCRIPT_DIR = "/opt/st_tools"
# Phase 57：st_cellchat_v2 跨镜像分发——脚本在 bio 镜像的 sc_tools
_SC_SCRIPT_DIR = "/opt/sc_tools"
# Phase 68：st_niche_scan 全 niche 批量（串行多 niche，档位 ×2 于单 niche）
_NICHE_SCAN_TIMEOUT = 3600
_ST_INTEGRATE_TIMEOUT = 1800


def _err(exc: BioRunError) -> dict[str, str]:
    """BioRunError → 工具错误输出（ToolHandler 透传 error_code）。"""
    return {"error_code": exc.error_code, "error_message": str(exc)}


def register_l3_spatial(
    registry: ToolRegistry, runner: BioRunner,
    *, st_image: str = "feishu-research-agent/bio:st-cpu-latest",
    bio_image: str = "feishu-research-agent/bio:cpu-latest",
    st_deconvolve_timeout: int = 3600,
) -> None:
    """注册 st_* 工具（runner 由 runtime 装配后传入）。

    st_deconvolve_timeout：cell2location 反卷积独立超时（训练耗时，
    由 settings.st_deconvolve_timeout_sec 注入覆盖）。
    bio_image：Phase 57 st_cellchat_v2 跨镜像分发目标——CellChat v2
    单点安装在 bio 镜像（R 栈不在 st 镜像重复安装），经同一 WS_ROOT
    卷直读 st processed.h5ad。
    """

    def st_load(*, path: str) -> dict[str, Any]:
        """读入空间转录组数据（visium/h5ad/mtx+coords）→ dataset_ref + 概要。"""
        try:
            mount_root, rel, host = runner.resolve_data_path(path)
            # .h5ad 文件按文件级 hash、目录按聚合 hash——与 load.py 的
            # suffix 探测同一口径（修复前 h5ad 路径在宿主侧即抛
            # SC_FILE_NOT_FOUND，真实 Visium 验收发现）。
            dataset_id = (compute_dataset_id(host)
                          if host.lower().endswith(".h5ad")
                          else compute_dataset_id_dir(host))
            out = runner.run(
                "load", {"path": rel, "dataset_id": dataset_id},
                mounts=[(mount_root, "/data")],
                image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_qc(*, dataset_ref: str, min_genes: int = 50,
              max_genes: int = 6000, max_mt_pct: float = 20.0) -> dict[str, Any]:
        """spot 级质控过滤 → filtered.h5ad + 前后统计。"""
        try:
            out = runner.run(
                "qc", {
                    "dataset_id": dataset_ref,
                    "min_genes": min_genes, "max_genes": max_genes,
                    "max_mt_pct": max_mt_pct,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_process(*, dataset_ref: str, n_pcs: int = 30,
                   resolution: float = 1.0, n_neighbors: int = 15) -> dict[str, Any]:
        """空间邻域 + PCA + Leiden 空间域 → processed.h5ad + 空间着色图。"""
        try:
            out = runner.run(
                "process", {
                    "dataset_id": dataset_ref, "n_pcs": n_pcs,
                    "resolution": resolution, "n_neighbors": n_neighbors,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_markers(*, dataset_ref: str, method: str = "wilcoxon",
                   top_n: int = 10) -> dict[str, Any]:
        """空间域差异基因 → markers JSON + dotplot.png。"""
        try:
            out = runner.run(
                "markers", {
                    "dataset_id": dataset_ref, "method": method,
                    "top_n": top_n,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=1200)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_plot(*, dataset_ref: str, genes: list[str] | None = None,
                color_by: str = "") -> dict[str, Any]:
        """spatial 着色图（基因表达/obs 列）→ png 列表。"""
        try:
            out = runner.run(
                "plot", {
                    "dataset_id": dataset_ref,
                    "genes": parse_gene_list(genes), "color_by": color_by,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_domains(*, dataset_ref: str, method: str = "banksy",
                   resolution: float = 1.0) -> dict[str, Any]:
        """空间域细分（banksy-lite 邻域均值特征 / leiden）→ 新域 + ARI 对比。"""
        try:
            out = runner.run(
                "domains", {
                    "dataset_id": dataset_ref, "method": method,
                    "resolution": resolution,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=1200)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_commot(*, dataset_ref: str, species: str = "",
                  dis_thr: float = 200.0) -> dict[str, Any]:
        """配体受体空间通讯（COMMOT + CellChat 库）→ 通讯图 + top LR 对。"""
        try:
            out = runner.run(
                "commot", {
                    "dataset_id": dataset_ref,
                    "species": resolve_species(
                        getattr(runner, "workspace_root", ""),
                        dataset_ref, species),
                    "dis_thr": dis_thr,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_deconvolve(*, dataset_ref: str, sc_ref: str,
                      max_epochs: int = 30000,
                      n_cells_per_location: float = 8.0,
                      detection_alpha: float = 20.0,
                      ref_label_col: str = "",
                      ref_epochs: int = 250,
                      num_samples: int = 1000,
                      ref_max_cells_per_type: int = 0,
                      deconv_timeout: int = st_deconvolve_timeout) -> dict[str, Any]:
        """cell2location 反卷积：sc_ref 为 sc 产物 dataset_ref（12hex）或
        白名单内参考 h5ad 路径。CPU 口径：max_epochs 默认 30000 仅 GPU
        可行，CPU 实用 2000；大参考配 ref_max_cells_per_type 分层限帽。"""
        import re as _re
        try:
            if _re.fullmatch(r"[0-9a-f]{12}", sc_ref):
                args = {"dataset_id": dataset_ref,
                        "sc_ref_dataset": sc_ref,
                        "ref_label_col": ref_label_col,
                        "max_epochs": max_epochs,
                        "ref_epochs": ref_epochs,
                        "num_samples": num_samples,
                        "ref_max_cells_per_type": ref_max_cells_per_type,
                        "n_cells_per_location": n_cells_per_location,
                        "detection_alpha": detection_alpha}
                mounts = None
            else:
                mount_root, rel, _ = runner.resolve_data_path(sc_ref)
                args = {"dataset_id": dataset_ref,
                        "sc_ref_path": rel,
                        "ref_label_col": ref_label_col,
                        "max_epochs": max_epochs,
                        "ref_epochs": ref_epochs,
                        "num_samples": num_samples,
                        "ref_max_cells_per_type": ref_max_cells_per_type,
                        "n_cells_per_location": n_cells_per_location,
                        "detection_alpha": detection_alpha}
                mounts = [(mount_root, "/data")]
            out = runner.run("deconvolve", args, mounts=mounts,
                             image=st_image, script_dir=_ST_SCRIPT_DIR,
                             timeout_sec=deconv_timeout)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_stats(*, dataset_ref: str, analysis: str,
                 mode: str = "moran",
                 genes: list[str] | None = None,
                 cluster_key: str = "", n_perms: int = 1000,
                 coord_type: str = "grid", n_neighs: int = 6) -> dict[str, Any]:
        """空间统计三分析（Moran/Geary 自相关、共现、邻域富集）。"""
        try:
            out = runner.run(
                "stats", {
                    "dataset_id": dataset_ref, "analysis": analysis,
                    "mode": mode, "genes": parse_gene_list(genes),
                    "cluster_key": cluster_key, "n_perms": n_perms,
                    "coord_type": coord_type, "n_neighs": n_neighs,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_cnv(*, dataset_ref: str, annotation_key: str = "",
               ref_groups: list[str] | None = None,
               resolution: float = 1.0) -> dict[str, Any]:
        """空间 CNV 推断与恶性 spot 判定（infercnvpy）→ 组织定位图。"""
        try:
            out = runner.run(
                "cnv", {
                    "dataset_id": dataset_ref,
                    "annotation_key": annotation_key,
                    "ref_groups": ref_groups,
                    "resolution": resolution,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=3600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_niche(*, dataset_ref: str, k: int = 12) -> dict[str, Any]:
        """空间生态位重构：细胞型组成 ward 层次聚类 → niche 标签写回。"""
        try:
            out = runner.run(
                "niche", {"dataset_id": dataset_ref, "k": k},
                image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_vicinity(*, dataset_ref: str, max_layers: int = 5,
                    coord_type: str = "grid") -> dict[str, Any]:
        """肿瘤邻域分层：恶性种子沿空间邻居图 BFS 分层写回。"""
        try:
            out = runner.run(
                "vicinity", {
                    "dataset_id": dataset_ref,
                    "max_layers": max_layers,
                    "coord_type": coord_type,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_misty(*, dataset_ref: str, n_hvg: int = 50,
                 bandwidth: float = 0,
                 extra_mode: str = "hvg") -> dict[str, Any]:
        """多视图空间建模（liana MISTy）：组成=intra，HVG/通路=juxta/para。"""
        try:
            out = runner.run(
                "misty", {
                    "dataset_id": dataset_ref,
                    "n_hvg": n_hvg,
                    "bandwidth": bandwidth,
                    "extra_mode": extra_mode,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_trajectory(*, dataset_ref: str, root_mode: str = "marker",
                      root_marker: str = "",
                      root_layer: str = "tumor") -> dict[str, Any]:
        """空间拟时序：表达图 DPT + PAGA 映射回组织坐标。"""
        try:
            out = runner.run(
                "trajectory", {
                    "dataset_id": dataset_ref,
                    "root_mode": root_mode,
                    "root_marker": root_marker,
                    "root_layer": root_layer,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=600)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_cellchat_v2(*, dataset_ref: str,
                       celltype_col: str = "spatial_domain",
                       species: str = "", min_cells: int = 10,
                       top_n: int = 30, max_cells_per_group: int = 0,
                       interaction_range: float = 250.0) -> dict[str, Any]:
        """空间细胞通讯 v2（Phase 57）：CellChat v2 空间引擎（距离约束+
        接触依赖），bio 镜像跨镜像分发（R 栈单点安装）。"""
        try:
            out = runner.run(
                "cellchat_v2", {
                    "dataset_id": dataset_ref,
                    "celltype_col": celltype_col,
                    "species": resolve_species(
                        getattr(runner, "workspace_root", ""),
                        dataset_ref, species),
                    "min_cells": min_cells,
                    "top_n": top_n,
                    "max_cells_per_group": max_cells_per_group,
                    "interaction_range": interaction_range,
                }, image=bio_image, script_dir=_SC_SCRIPT_DIR,
                timeout_sec=3600, memory="32g")
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_nichenet(*, dataset_ref: str, geneset: list[str],
                    receiver_niche: str,
                    groupby: str = "spatial_domain", max_rings: int = 1,
                    species: str = "", top_n_ligands: int = 30,
                    min_expr: float = 0.1, knn: int = 6) -> dict[str, Any]:
        """空间配体活性优先级（Phase 65）：NicheNet + kNN BFS 分环——
        sender 收窄为 receiver niche 的接壤邻域（r1..max_rings），
        min_expr 门控在 sender 侧实现邻近约束；bio 镜像跨镜像分发
        （nichenetr R 桥单点安装），KB 轻量桥零改动复用。"""
        try:
            out = runner.run(
                "st_nichenet", {
                    "dataset_id": dataset_ref,
                    "geneset": geneset,
                    "receiver_niche": receiver_niche,
                    "groupby": groupby,
                    "max_rings": max_rings,
                    "species": resolve_species(
                        getattr(runner, "workspace_root", ""),
                        dataset_ref, species),
                    "top_n_ligands": top_n_ligands,
                    "min_expr": min_expr,
                    "knn": knn,
                }, image=bio_image, script_dir=_SC_SCRIPT_DIR,
                timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_niche_scan(*, dataset_ref: str, groupby: str = "spatial_domain",
                      min_spots: int = 5, n_geneset: int = 30,
                      max_rings: int = 1, knn: int = 6, species: str = "",
                      top_n_ligands: int = 30,
                      min_expr: float = 0.1) -> dict[str, Any]:
        """全 niche NicheNet 配体扫描（Phase 68）：枚举 groupby 全部取值
        （≥min_spots），逐 niche 以其上调 top 基因为 geneset 串行调用
        st_nichenet，墙钟预算自管（单 niche 上限 1800s、收尾余量 180s），
        汇总 ligand×niche aupr 矩阵 + 热图 + summary；单 niche 失败不
        中断，记入 failures 继续。"""
        try:
            out = runner.run(
                "st_niche_scan", {
                    "dataset_id": dataset_ref,
                    "groupby": groupby,
                    "min_spots": min_spots,
                    "n_geneset": n_geneset,
                    "max_rings": max_rings,
                    "knn": knn,
                    "species": resolve_species(
                        getattr(runner, "workspace_root", ""),
                        dataset_ref, species),
                    "top_n_ligands": top_n_ligands,
                    "min_expr": min_expr,
                }, image=bio_image, script_dir=_SC_SCRIPT_DIR,
                timeout_sec=_NICHE_SCAN_TIMEOUT)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_integrate(*, dataset_refs: list[str], method: str = "harmony",
                     n_top_hvg: int = 2000, n_pcs: int = 30,
                     n_neighbors: int = 15, resolution: float = 1.0,
                     slice_col: str = "slice",
                     spatial_offset: bool = False) -> dict[str, Any]:
        """多切片空间数据整合（Phase 69）：≥2 个 st 数据集 merge →
        全局预处理 → harmony/bbknn 去批次 → UMAP + leiden **表达域**。
        bio 镜像跨镜像分发（harmonypy 单点安装），st 镜像零增重；
        不做空间邻域图（跨切片坐标无意义），下游空间分析按切片回
        各原始数据集。"""
        try:
            out = runner.run(
                "st_integrate", {
                    "dataset_refs": dataset_refs,
                    "method": method,
                    "n_top_hvg": n_top_hvg,
                    "n_pcs": n_pcs,
                    "n_neighbors": n_neighbors,
                    "resolution": resolution,
                    "slice_col": slice_col,
                    "spatial_offset": spatial_offset,
                }, image=bio_image, script_dir=_SC_SCRIPT_DIR,
                timeout_sec=_ST_INTEGRATE_TIMEOUT)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    registry.register(ToolSpec(
        name="st_load",
        description=(
            "读入本地空间转录组数据（spaceranger 输出目录 / 含空间坐标的 "
            ".h5ad / matrix.mtx+coords.csv 目录）。输出 dataset_ref（下游 "
            "st_* 工具用 <node_id>.dataset_ref 引用）、n_spots、n_genes。"
            "path 必须在管理员允许的数据目录内。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "本地数据路径（spaceranger 目录或 h5ad）"},
            },
            "required": ["path"],
        },
        risk_level="L1_compute",
        handler=st_load,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="st_qc",
        description=(
            "spot 级质控过滤（每 spot 最小/最大基因数、最大线粒体比例%）→ "
            "filtered.h5ad。输出过滤前后 spot/基因数与剔除数。"
            "注意：小规模/测试数据每细胞（spot）基因数可能仅几十，min_genes 过大会"
            "全滤光——若失败，错误消息含 genes/cell 分布（median/p90/max），请按"
            "median 以下调低 min_genes 重试。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_load 输出的 dataset_ref"},
                "min_genes": {"type": "integer", "default": 50},
                "max_genes": {"type": "integer", "default": 6000},
                "max_mt_pct": {"type": "number", "default": 20.0},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_qc,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="st_process",
        description=(
            "空间转录组标准流程：归一化→HVG→PCA→空间邻域图→Leiden 空间域"
            "聚类，产出 processed.h5ad、umap.png 与 spatial_domains.png"
            "（空间域着色图，st 核心输出）。建议先跑 st_qc。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "n_pcs": {"type": "integer", "default": 30},
                "resolution": {"type": "number", "default": 1.0},
                "n_neighbors": {"type": "integer", "default": 15},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_process,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="st_markers",
        description=(
            "每个空间域的差异基因（rank_genes_groups），输出每域 top 基因"
            "（gene/score/log2fc）与 dotplot.png。需先跑 st_process。"
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
        handler=st_markers,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="st_plot",
        description=(
            "空间着色图（spatial scatter）：genes 按基因表达着色（≤6 个），"
            "或 color_by 按 obs 列（如 spatial_domain）着色。输出 png 路径"
            "列表。需先跑 st_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "genes": {"type": "array", "items": {"type": "string"},
                          "maxItems": 6,
                          "description": "基因符号列表（如 MARKER_D1）"},
                "color_by": {"type": "string",
                             "description": "obs 列名（如 spatial_domain）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_plot,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="st_domains",
        description=(
            "空间域细分：banksy 方法（邻域均值特征增强的空间感知 Leiden，"
            "Banksy-lite）或 leiden 重聚类，输出新空间域着色图、与既有 "
            "leiden 域的 ARI 一致性与对比图。需先跑 st_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "method": {"type": "string", "enum": ["banksy", "leiden"],
                           "default": "banksy"},
                "resolution": {"type": "number", "default": 1.0},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_domains,
        timeout_sec=1200,
    ))
    registry.register(ToolSpec(
        name="st_commot",
        description=(
            "配体受体空间通讯分析（COMMOT 最优传输 + CellChat 库）：输出 "
            "top 通讯通路的方向图（sender/receiver）、空间域×通路通讯强度"
            "热图与 top 配体受体对。species 选 human/mouse；dis_thr 通讯"
            "距离阈值（单位同空间坐标，visium 默认 200）。需先跑 st_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "species": {"type": "string",
                            "enum": ["human", "mouse", ""],
                            "description": "CellChat LR 库物种；留空按基因"
                                           "符号风格自动检测"},
                "dis_thr": {"type": "number", "default": 200},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_commot,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="st_deconvolve",
        description=(
            "cell2location 细胞类型反卷积：估算每个 spot 的各细胞类型丰度，"
            "输出 n_cell_types、cell_types、mean_abundance 与各细胞类型空间"
            "分布图。sc_ref 双来源——同任务 sc_process 输出的 dataset_ref"
            "（12 位十六进制，复用其 leiden 注释），或白名单内参考 h5ad 路径"
            "（注释列可用 ref_label_col 指定，留空自动探测 cell_type/leiden/"
            "cluster 等）。需先跑 st_qc/st_process；参考为 workspace 数据集时"
            "需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_load 输出的 dataset_ref"},
                "sc_ref": {"type": "string",
                           "description": "sc 参考数据集 id（12 位 hex，"
                                          "sc_process 输出）或白名单内 h5ad 路径"},
                "max_epochs": {"type": "integer", "default": 30000,
                               "description": "空间模型训练轮数；CPU 实用"
                                              " 2000（默认 30000 仅 GPU 可行）"},
                "n_cells_per_location": {"type": "number", "default": 8},
                "detection_alpha": {"type": "number", "default": 20},
                "ref_label_col": {"type": "string", "default": "",
                                  "description": "参考 h5ad 的细胞类型注释列"
                                                 "（留空自动探测）"},
                "ref_epochs": {"type": "integer", "default": 250,
                               "description": "参考签名模型训练轮数"},
                "num_samples": {"type": "integer", "default": 1000,
                                "description": "后验采样数（export_posterior）"},
                "ref_max_cells_per_type": {
                    "type": "integer", "default": 0,
                    "description": "参考每细胞类型限帽（0=不抽样）；大参考"
                                   "（万级细胞）CPU 必须限帽（如 150）"},
            },
            "required": ["dataset_ref", "sc_ref"],
        },
        risk_level="L1_compute",
        handler=st_deconvolve,
        timeout_sec=st_deconvolve_timeout,
    ))
    registry.register(ToolSpec(
        name="st_stats",
        description=(
            "空间统计分析四合一：autocorr（Moran's I/Geary's C 空间自相关，"
            "识别空间可变基因，输出逐基因统计表与 top4 空间分布图）、"
            "cooccurrence（簇间空间共现曲线）、nhood_enrichment（簇间邻域"
            "富集 zscore 热图）、centrality（图中心性 degree/clustering/"
            "closeness + 簇间互作矩阵，识别组织枢纽簇）。cluster_key 留空"
            "自动回退 spatial_domain→banksy_domain→leiden→clusters。"
            "需先跑 st_process；cooccurrence/nhood_enrichment/centrality "
            "建议先跑 st_domains。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string"},
                "analysis": {"type": "string",
                             "enum": ["autocorr", "cooccurrence",
                                      "nhood_enrichment", "centrality"]},
                "mode": {"type": "string", "enum": ["moran", "geary"],
                         "default": "moran",
                         "description": "仅 autocorr"},
                "genes": {"type": "array", "items": {"type": "string"},
                          "description": "仅 autocorr：显式基因列表"
                                         "（空=高变基因前 50）"},
                "cluster_key": {"type": "string", "default": "",
                                "description": "仅后三个分析：obs 列名"
                                               "（留空自动回退）"},
                "n_perms": {"type": "integer", "default": 1000,
                            "description": "仅 nhood_enrichment：排列次数"},
                "coord_type": {"type": "string", "enum": ["grid", "generic"],
                               "default": "grid",
                               "description": "邻域图类型（仅 processed 无"
                                              "既有邻域图时兜底生效）"},
                "n_neighs": {"type": "integer", "default": 6},
            },
            "required": ["dataset_ref", "analysis"],
        },
        risk_level="L1_compute",
        handler=st_stats,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="st_cnv",
        description=(
            "空间 CNV 推断与恶性 spot 判定（infercnvpy）：从原始 counts "
            "推断拷贝数变异，以内置非恶性清单（或 ref_groups 显式指定）为"
            "参考做恶性判定，输出染色体热图、cnv_score/cnv_subclone 空间"
            "组织定位图与统计表；cnv_score/is_malignant/cnv_subclone 写回 "
            "processed.h5ad（st_plot 可着色）。annotation_key 指定 spot "
            "注释列，留空回退 cell_type→spatial_domain→leiden，特殊值 "
            "'deconv' 用 st_deconvolve 产物的权重最大型（可命中内置非恶性"
            "清单）。需先跑 st_load/st_process；注释列为数字簇时无法匹配"
            "参考清单，请传 ref_groups 或改用 annotation_key='deconv'。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_load 输出的 dataset_ref"},
                "annotation_key": {"type": "string", "default": "",
                                   "description": "spot 注释列名，或 'deconv'"},
                "ref_groups": {"type": "array", "items": {"type": "string"},
                               "description": "显式参考组（注释取值列表）"},
                "resolution": {"type": "number", "default": 1.0,
                               "description": "亚克隆 leiden 分辨率"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_cnv,
        timeout_sec=3600,
    ))
    registry.register(ToolSpec(
        name="st_niche",
        description=(
            "空间生态位（niche）重构：基于 st_deconvolve 的细胞型组成矩阵"
            "（行归一化）做 ward 层次聚类，把组成相似的 spot 聚为生态位，"
            "输出 niche 空间着色图、niche×细胞型组成热图与矩阵 csv；"
            "obs['niche'] 写回 processed.h5ad（st_plot 可着色、st_stats "
            "可作 cluster_key）。k 为 niche 数（默认 12）。需先跑 "
            "st_process 与 st_deconvolve。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_load 输出的 dataset_ref"},
                "k": {"type": "integer", "default": 12,
                      "description": "niche 数（2 ≤ k < n_spots）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_niche,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="st_vicinity",
        description=(
            "肿瘤邻域分层：以 st_cnv 判定的恶性 spot（obs['is_malignant']）"
            "为种子，沿空间邻居图 BFS 向外分层（tumor / L1..Ln / distal），"
            "刻画肿瘤核心→侵袭前沿→远端梯度；输出分层空间着色图、层尺寸"
            "csv，deconv.h5ad 存在时追加层×细胞型组成热图（免疫/基质随"
            "距离梯度）；obs['vicinity'] 写回 processed.h5ad（st_plot 可"
            "着色、st_stats 可作 cluster_key）。需先跑 st_process 与 "
            "st_cnv。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_load 输出的 dataset_ref"},
                "max_layers": {"type": "integer", "default": 5,
                               "description": "BFS 最大层数（1..10）"},
                "coord_type": {"type": "string", "default": "grid",
                               "enum": ["grid", "generic"],
                               "description": "补建邻居图坐标类型"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_vicinity,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="st_misty",
        description=(
            "多视图空间建模（liana MISTy）：以细胞型组成（st_deconvolve "
            "产物）为 intra 目标视图、top HVG 基因表达为 juxta（紧邻）+ "
            "para（旁分泌半径）预测视图，随机森林逐目标建模，回答哪些"
            "细胞型/基因在空间上互相解释；输出视图贡献热图、para 视图 "
            "target×predictor 重要性热图与两个全量 csv。bandwidth 为 "
            "para 半径（坐标单位），0=自动（5×中位近邻距）。需先跑 "
            "st_process 与 st_deconvolve。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_load 输出的 dataset_ref"},
                "n_hvg": {"type": "integer", "default": 50,
                          "description": "extra 视图 top HVG 数（10..500）"},
                "bandwidth": {"type": "number", "default": 0,
                              "description": "para 半径；0=自动"},
                "extra_mode": {"type": "string", "default": "hvg",
                               "enum": ["hvg", "progeny", "tf"],
                               "description": "extra 视图来源：hvg=top HVG "
                                              "表达；progeny=PROGENy 14 通路"
                                              "活性；tf=CollecTRI TF 活性"
                                              "（decoupler MLM，离线）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_misty,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="st_trajectory",
        description=(
            "空间拟时序：表达邻居图扩散伪时序（scanpy diffmap + DPT）+ "
            "PAGA 域拓扑，映射回组织空间坐标，回答表达进程是否沿空间"
            "方向展开。root_mode=marker：root_marker 表达最高 spot 为根"
            "（空则 spot #0）；root_mode=vicinity：st_vicinity 层"
            "（root_layer，默认 tumor）内度中位 spot 为根。需先跑 "
            "st_process；vicinity 模式需先跑 st_vicinity。产物 "
            "trajectory/：pseudotime.csv + 空间着色图 + PAGA 空间质心图"
            "（obs 有 vicinity 时附分层 boxplot + Spearman ρ）；"
            "dpt_pseudotime 写回 obs 供 st_plot 叠加。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_load 输出的 dataset_ref"},
                "root_mode": {"type": "string", "default": "marker",
                              "enum": ["marker", "vicinity"],
                              "description": "定根模式：marker=基因表达最高"
                                             " spot；vicinity=st_vicinity 层"},
                "root_marker": {"type": "string", "default": "",
                                "description": "marker 模式根基因 symbol"},
                "root_layer": {"type": "string", "default": "tumor",
                               "description": "vicinity 模式根层"
                                              "（tumor/distal/L1..Ln）"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_trajectory,
        timeout_sec=600,
    ))
    registry.register(ToolSpec(
        name="st_cellchat_v2",
        description=(
            "空间细胞通讯 v2（Phase 57）：R 版 CellChat 2.2 空间引擎——"
            "以 spot 空间坐标作通讯概率约束（distance.use + "
            "interaction.range µm）并对 Cell-Cell Contact 类互作启用"
            "接触依赖（knn=6），配合 CellChatDB v2（3233 互作，离线"
            "内置）。与 st_commot（Python/OT 路线）互补：通路级聚合 + "
            "11 种网络中心性（hub/authority...）+ 互作证据（KEGG/PMID）。"
            "输出显著 LR 对 top 表、通路级 top 表、lr/pathway/centrality/"
            "counts csv、top LR dotplot、互作计数热图、hub 中心性热图。"
            "需先跑 st_process（obsm.spatial 自动触发空间模式；visium "
            "fullres 像素坐标自动按 scalefactors 折算 µm，缺失时按原"
            "单位口径并在 note 提示）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_process 输出的 dataset_ref"},
                "celltype_col": {
                    "type": "string", "default": "spatial_domain",
                    "description": "标签列：spatial_domain（默认）/"
                                   "st_deconvolute 反卷积主型列等"},
                "species": {"type": "string",
                            "enum": ["human", "mouse", ""],
                            "description": "CellChatDB 物种；留空按基因"
                                           "符号风格自动检测"},
                "min_cells": {"type": "integer", "default": 10,
                              "description": "标签组最少 spot 数（低于剔除）"},
                "top_n": {"type": "integer", "default": 30, "maximum": 100},
                "max_cells_per_group": {
                    "type": "integer", "default": 0,
                    "description": "每标签组抽样上限，0=全量"},
                "interaction_range": {
                    "type": "number", "default": 250.0,
                    "description": "互作距离约束（µm）；visium spot 中心"
                                   "距 100µm，250≈2.5 spot"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_cellchat_v2,
        timeout_sec=3600,
        memory="32g",
    ))
    registry.register(ToolSpec(
        name="st_nichenet",
        description=(
            "空间配体活性优先级（Phase 65，NicheNet + 空间邻域约束）："
            "回答『哪些配体最可能调控我指定空间 niche（receiver）中 "
            "geneset 的表达』——sender 不再是任意群，而是与 receiver "
            "niche 物理接壤的 spot 邻域（kNN 图 BFS 一环，max_rings 可"
            "扩），min_expr 门控在 sender 侧过滤低表达 spot，实现『邻近"
            "才通讯』。先验网络与活性排序逻辑同 sc_nichenet"
            "（aupr_corrected 排序）。输出按 receiver niche 加后缀："
            "ligand_activities_{niche}.csv、ligand_target_links_{niche}.csv、"
            "活性条图、配体×靶基因热图、空间分环 rings_{niche}.png（多 "
            "receiver 运行互不覆写）。适用：需要空间邻接证据的配体优先级（与 "
            "st_commot/st_cellchat_v2 的 L-R 机制证据互补）。geneset 与"
            "先验靶空间交集 <5 或可测配体 <3 时报错。需先跑 st_process"
            "（obsm.spatial）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_process 输出的 dataset_ref"},
                "geneset": {"type": "array", "items": {"type": "string"},
                            "minItems": 5,
                            "description": "receiver niche 侧目标基因集"
                                           "（如该 niche 高表达基因/DE "
                                           "上调，建议 ≥10）"},
                "receiver_niche": {
                    "type": "string",
                    "description": "receiver 分组取值（obs[groupby]==该值"
                                   " 的 spots 为 BFS core，如 "
                                   "spatial_domain 列的某个域名）"},
                "groupby": {"type": "string", "default": "spatial_domain",
                            "description": "分组列名（receiver_niche 取值"
                                           "来源）"},
                "max_rings": {"type": "integer", "default": 1,
                              "minimum": 1, "maximum": 5,
                              "description": "sender 取 BFS 1..max_rings "
                                             "环（1=仅接壤邻域）"},
                "species": {"type": "string",
                            "enum": ["human", "mouse", ""],
                            "description": "先验网络物种；空=自动探测"},
                "top_n_ligands": {"type": "integer", "default": 30,
                                  "maximum": 100,
                                  "description": "调控边/图表取 top N 配体"},
                "min_expr": {"type": "number", "default": 0.1,
                             "description": "sender spot 表达比例门控"
                                            "（邻近+表达双约束）"},
                "knn": {"type": "integer", "default": 6,
                        "minimum": 4, "maximum": 20,
                        "description": "空间 kNN 图近邻数"},
            },
            "required": ["dataset_ref", "geneset", "receiver_niche"],
        },
        risk_level="L1_compute",
        handler=st_nichenet,
        timeout_sec=1800,
    ))
    registry.register(ToolSpec(
        name="st_niche_scan",
        description=(
            "全 niche NicheNet 配体扫描（Phase 68，串行批量）：无需人工"
            "指定 receiver niche 与 geneset——自动枚举 groupby 全部取值"
            "（spot 数 ≥min_spots，计数降序），逐 niche 以其上调 top "
            "n_geneset 基因为 geneset（niche 内 vs 其余 spot 均值差，剔"
            "线粒体/核糖体前缀）串行调用 st_nichenet（kNN BFS 邻域约束"
            "同单 niche 版），墙钟预算自管（单 niche 上限 1800s、收尾余"
            "量 180s；预算不足的剩余 niche 记 TIME_BUDGET 失败不中断，"
            "单 niche 失败也照常继续）。输出 ligand×niche 的 "
            "aupr_corrected 汇总矩阵 CSV + 热图 + summary JSON（含 "
            "n_niches_ok/failed、failures 明细、全体 niche 共有配体），"
            "另有逐 niche 的 ligand_activities_{niche}.csv 等五件套"
            "（slug 命名互不覆写）。适用：无先验 niche 选择时的全景配体"
            "优先级排序（aupr 原值不做 z-score，横向比较看每列内排名）。"
            "需先跑 st_process（obsm.spatial）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "st_process 输出的 dataset_ref"},
                "groupby": {"type": "string", "default": "spatial_domain",
                            "description": "niche 枚举的分组列名"},
                "min_spots": {"type": "integer", "default": 5,
                              "minimum": 1,
                              "description": "niche 入选的最少 spot 数"},
                "n_geneset": {"type": "integer", "default": 30,
                              "minimum": 10, "maximum": 100,
                              "description": "每 niche 自动取的上调基因数"
                                             "（作 geneset）"},
                "max_rings": {"type": "integer", "default": 1,
                              "minimum": 1, "maximum": 5,
                              "description": "sender 取 BFS 1..max_rings "
                                             "环（1=仅接壤邻域）"},
                "knn": {"type": "integer", "default": 6,
                        "minimum": 4, "maximum": 20,
                        "description": "空间 kNN 图近邻数"},
                "species": {"type": "string",
                            "enum": ["human", "mouse", ""],
                            "description": "先验网络物种；空=自动探测"},
                "top_n_ligands": {"type": "integer", "default": 30,
                                  "maximum": 100,
                                  "description": "逐 niche 结果取 top N 配体"},
                "min_expr": {"type": "number", "default": 0.1,
                             "description": "sender spot 表达比例门控"},
            },
            "required": ["dataset_ref"],
        },
        risk_level="L1_compute",
        handler=st_niche_scan,
        timeout_sec=_NICHE_SCAN_TIMEOUT,
    ))
    registry.register(ToolSpec(
        name="st_integrate",
        description=(
            "多切片空间数据整合（Phase 69）：≥2 个 st 数据集合并为一张"
            "图——全局 normalize→HVG→PCA 后 harmony（默认）/bbknn 去批次"
            "效应，UMAP + leiden **表达域**聚类（跨切片坐标无意义，不做"
            "空间邻域图；下游空间分析按切片回各原始数据集）。输入走各片 "
            "filtered/raw（counts 形态统一重预处理），建议先对各片 st_qc。"
            "输出新 dataset_ref、双联 UMAP 与按切片空间分布图"
            "（spatial_offset=true 时坐标平移并排，仅展示用，勿再喂给"
            "空间 kNN 工具）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_refs": {"type": "array", "items": {"type": "string"},
                                 "minItems": 2,
                                 "description": "≥2 个 st 数据集 ref"
                                                "（st_load 输出）"},
                "method": {"type": "string", "default": "harmony",
                           "enum": ["harmony", "bbknn"],
                           "description": "harmony=harmonypy；"
                                          "bbknn=批次感知 kNN"},
                "n_top_hvg": {"type": "integer", "default": 2000,
                              "minimum": 100},
                "n_pcs": {"type": "integer", "default": 30,
                          "minimum": 5, "maximum": 100},
                "n_neighbors": {"type": "integer", "default": 15,
                                "minimum": 5, "maximum": 100},
                "resolution": {"type": "number", "default": 1.0},
                "slice_col": {"type": "string", "default": "slice",
                              "description": "合并后批次列名"},
                "spatial_offset": {"type": "boolean", "default": False,
                                   "description": "切片空间坐标平移并排"
                                                  "（仅展示用）"},
            },
            "required": ["dataset_refs"],
        },
        risk_level="L1_compute",
        handler=st_integrate,
        timeout_sec=_ST_INTEGRATE_TIMEOUT,
    ))
