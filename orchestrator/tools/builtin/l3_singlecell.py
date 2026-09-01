"""Phase 20：单细胞 sc_* 工具注册（spec §4，5 个 L1_compute 工具）。

BioRunner 由 runtime 组装注入（image/workspace/data_roots 可配）；
handler 捕获 BioRunError 转工具级 error_code/error_message。
"""
from __future__ import annotations

from orchestrator.tools.bio.bio_runner import (
    BioRunner,
    BioRunError,
    compute_dataset_id,
)
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


def _err(exc: BioRunError) -> dict:
    """BioRunError → 工具错误输出（ToolHandler 透传 error_code）。"""
    return {"error_code": exc.error_code, "error_message": str(exc)}


def register_l3_singlecell(registry: ToolRegistry, runner: BioRunner) -> None:
    """注册 sc_* 5 工具（runner 由 runtime 装配后传入）。"""

    def sc_load(*, path: str, format: str = "auto") -> dict:  # noqa: A002
        """读入本地单细胞数据 → dataset_ref + 概要统计。"""
        try:
            mount_root, rel, host = runner.resolve_data_path(path)
            dataset_id = compute_dataset_id(host)
            out = runner.run(
                "load",
                {"path": rel, "dataset_id": dataset_id},
                mounts=[(mount_root, "/data")],
            )
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_qc(*, dataset_ref: str, min_genes: int = 600,
              min_cells: int = 3, max_mt_pct: float = 20.0) -> dict:
        """质控过滤 → filtered.h5ad + 前后统计。"""
        try:
            out = runner.run("qc", {
                "dataset_id": dataset_ref,
                "min_genes": min_genes, "min_cells": min_cells,
                "max_mt_pct": max_mt_pct,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_process(*, dataset_ref: str, n_top_hvg: int = 2000,
                   n_pcs: int = 50, n_neighbors: int = 15,
                   resolution: float = 1.0) -> dict:
        """标准流程（归一化→HVG→PCA→UMAP→Leiden）→ processed.h5ad + umap.png。"""
        try:
            out = runner.run("process", {
                "dataset_id": dataset_ref,
                "n_top_hvg": n_top_hvg, "n_pcs": n_pcs,
                "n_neighbors": n_neighbors, "resolution": resolution,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_markers(*, dataset_ref: str, method: str = "wilcoxon",
                   top_n: int = 10) -> dict:
        """每簇差异基因 → markers JSON + dotplot.png。"""
        try:
            out = runner.run("markers", {
                "dataset_id": dataset_ref, "method": method,
                "top_n": top_n,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_plot(*, dataset_ref: str, genes: list[str],
                kind: str = "violin") -> dict:
        """指定基因画图（violin/umap_gene）→ png 路径列表。"""
        try:
            out = runner.run("plot", {
                "dataset_id": dataset_ref, "genes": genes, "kind": kind,
            })
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
