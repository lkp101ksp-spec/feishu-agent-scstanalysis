"""Phase 21：空间转录组 st_* 工具注册（spec §2.2，5 个 L1_compute 工具）。

与 l3_singlecell 同模式：runner 由 runtime 组装注入；st 脚本走 st 镜像
（run 时覆盖 image/script_dir）；handler 捕获 BioRunError 转错误输出。
"""
from __future__ import annotations

from orchestrator.tools.bio.bio_runner import (
    BioRunner,
    BioRunError,
    compute_dataset_id_dir,
    parse_gene_list,
)
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec

_ST_SCRIPT_DIR = "/opt/st_tools"


def _err(exc: BioRunError) -> dict:
    """BioRunError → 工具错误输出（ToolHandler 透传 error_code）。"""
    return {"error_code": exc.error_code, "error_message": str(exc)}


def register_l3_spatial(
    registry: ToolRegistry, runner: BioRunner,
    *, st_image: str = "feishu-research-agent/bio:st-cpu-latest",
) -> None:
    """注册 st_* 5 工具（runner 由 runtime 装配后传入）。"""

    def st_load(*, path: str) -> dict:
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
              max_genes: int = 6000, max_mt_pct: float = 20.0) -> dict:
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
                   resolution: float = 1.0, n_neighbors: int = 15) -> dict:
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
                   top_n: int = 10) -> dict:
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
                color_by: str = "") -> dict:
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
