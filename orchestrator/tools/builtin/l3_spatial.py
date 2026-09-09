"""Phase 21：空间转录组 st_* 工具注册（spec §2.2，7 个 L1_compute 工具）。

与 l3_singlecell 同模式：runner 由 runtime 组装注入；st 脚本走 st 镜像
（run 时覆盖 image/script_dir）；handler 捕获 BioRunError 转错误输出。
"""
from __future__ import annotations

from typing import Any

from orchestrator.tools.bio.bio_runner import (
    BioRunError,
    BioRunner,
    compute_dataset_id_dir,
    parse_gene_list,
)
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec

_ST_SCRIPT_DIR = "/opt/st_tools"


def _err(exc: BioRunError) -> dict[str, str]:
    """BioRunError → 工具错误输出（ToolHandler 透传 error_code）。"""
    return {"error_code": exc.error_code, "error_message": str(exc)}


def register_l3_spatial(
    registry: ToolRegistry, runner: BioRunner,
    *, st_image: str = "feishu-research-agent/bio:st-cpu-latest",
    st_deconvolve_timeout: int = 3600,
) -> None:
    """注册 st_* 8 工具（runner 由 runtime 装配后传入）。

    st_deconvolve_timeout：cell2location 反卷积独立超时（训练耗时，
    由 settings.st_deconvolve_timeout_sec 注入覆盖）。
    """

    def st_load(*, path: str) -> dict[str, Any]:
        """读入空间转录组数据（visium/h5ad/mtx+coords）→ dataset_ref + 概要。"""
        try:
            mount_root, rel, host = runner.resolve_data_path(path)
            dataset_id = compute_dataset_id_dir(host)
            out = runner.run(
                "load", {"path": rel, "dataset_id": dataset_id},
                mounts=[(mount_root, "/data")],
                image=st_image, script_dir=_ST_SCRIPT_DIR)
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
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
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
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
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
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
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
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
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
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_commot(*, dataset_ref: str, species: str = "human",
                  dis_thr: float = 200.0) -> dict[str, Any]:
        """配体受体空间通讯（COMMOT + CellChat 库）→ 通讯图 + top LR 对。"""
        try:
            out = runner.run(
                "commot", {
                    "dataset_id": dataset_ref, "species": species,
                    "dis_thr": dis_thr,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def st_deconvolve(*, dataset_ref: str, sc_ref: str,
                      max_epochs: int = 30000,
                      n_cells_per_location: float = 8.0,
                      detection_alpha: float = 20.0,
                      ref_label_col: str = "",
                      deconv_timeout: int = st_deconvolve_timeout) -> dict[str, Any]:
        """cell2location 反卷积：sc_ref 为 sc 产物 dataset_ref（12hex）或
        白名单内参考 h5ad 路径。"""
        import re as _re
        try:
            if _re.fullmatch(r"[0-9a-f]{12}", sc_ref):
                args = {"dataset_id": dataset_ref,
                        "sc_ref_dataset": sc_ref,
                        "ref_label_col": ref_label_col,
                        "max_epochs": max_epochs,
                        "n_cells_per_location": n_cells_per_location,
                        "detection_alpha": detection_alpha}
                mounts = None
            else:
                mount_root, rel, _ = runner.resolve_data_path(sc_ref)
                args = {"dataset_id": dataset_ref,
                        "sc_ref_path": rel,
                        "ref_label_col": ref_label_col,
                        "max_epochs": max_epochs,
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
                "species": {"type": "string", "enum": ["human", "mouse"],
                            "default": "human"},
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
                "max_epochs": {"type": "integer", "default": 30000},
                "n_cells_per_location": {"type": "number", "default": 8},
                "detection_alpha": {"type": "number", "default": 20},
                "ref_label_col": {"type": "string", "default": "",
                                  "description": "参考 h5ad 的细胞类型注释列"
                                                 "（留空自动探测）"},
            },
            "required": ["dataset_ref", "sc_ref"],
        },
        risk_level="L1_compute",
        handler=st_deconvolve,
        timeout_sec=st_deconvolve_timeout,
    ))
