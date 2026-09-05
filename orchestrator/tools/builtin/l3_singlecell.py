"""Phase 20/31/32：单细胞 sc_* 工具注册（spec §4；9 个 L1_compute 工具）。

BioRunner 由 runtime 组装注入（image/workspace/data_roots 可配）；
handler 捕获 BioRunError 转工具级 error_code/error_message。
"""
from __future__ import annotations

from orchestrator.tools.bio.bio_runner import (
    BioRunError,
    BioRunner,
    compute_dataset_id,
    parse_gene_list,
)
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec


def _err(exc: BioRunError) -> dict:
    """BioRunError → 工具错误输出（ToolHandler 透传 error_code）。"""
    return {"error_code": exc.error_code, "error_message": str(exc)}


def register_l3_singlecell(
    registry: ToolRegistry,
    runner: BioRunner,
    *,
    bio_use_gpu: bool = False,
    bio_gpu_image: str = "feishu-research-agent/bio:gpu-latest",
) -> None:
    """注册 sc_* 6 工具（runner 由 runtime 装配后传入）。

    bio_use_gpu=True 时 sc_process/sc_markers 切 GPU 镜像 + --gpus all
    （Phase 25，spec 2026-09-02-bio-gpu-image-design §1.5）。
    """

    def _accel() -> tuple[str | None, bool]:
        """GPU 开关分流：开→(gpu_image, True)；关→(None=runner 默认镜像, False)。"""
        if bio_use_gpu:
            return bio_gpu_image, True
        return None, False

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
            }, image=None, gpus=False)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_process(*, dataset_ref: str, n_top_hvg: int = 2000,
                   n_pcs: int = 50, n_neighbors: int = 15,
                   resolution: float = 1.0) -> dict:
        """标准流程（归一化→HVG→PCA→UMAP→Leiden）→ processed.h5ad + umap.png。"""
        image, gpus = _accel()
        try:
            out = runner.run("process", {
                "dataset_id": dataset_ref,
                "n_top_hvg": n_top_hvg, "n_pcs": n_pcs,
                "n_neighbors": n_neighbors, "resolution": resolution,
            }, image=image, gpus=gpus)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_markers(*, dataset_ref: str, method: str = "wilcoxon",
                   top_n: int = 10) -> dict:
        """每簇差异基因 → markers JSON + dotplot.png。"""
        image, gpus = _accel()
        try:
            out = runner.run("markers", {
                "dataset_id": dataset_ref, "method": method,
                "top_n": top_n,
            }, image=image, gpus=gpus)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_plot(*, dataset_ref: str, genes: list[str],
                kind: str = "violin") -> dict:
        """指定基因画图（violin/umap_gene）→ png 路径列表。"""
        try:
            out = runner.run("plot", {
                "dataset_id": dataset_ref,
                "genes": parse_gene_list(genes), "kind": kind,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_enrichment(*, dataset_ref: str, group: str = "",
                      gene_sets: list[str] | None = None,
                      top_n: int = 15, min_log2fc: float = 0.25,
                      method: str = "wilcoxon") -> dict:
        """富集分析（Phase 31）：DEG→ORA + GSEA → csv/png。"""
        try:
            out = runner.run("enrichment", {
                "dataset_id": dataset_ref, "group": group,
                "gene_sets": gene_sets or ["hallmark", "go_bp", "kegg"],
                "top_n": top_n, "min_log2fc": min_log2fc,
                "rank_method": method,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_score_genes(*, dataset_ref: str,
                       gene_sets: dict[str, list[str]]) -> dict:
        """基因集打分（Phase 32）：多基因集 score_genes → 分数/图。"""
        try:
            out = runner.run("score", {
                "dataset_id": dataset_ref, "gene_sets": gene_sets,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_metabolism(*, dataset_ref: str, top_n: int = 30) -> dict:
        """代谢通路活性（Phase 32）：KEGG 逐通路打分 → 簇均值+热图。"""
        try:
            out = runner.run("metabolism", {
                "dataset_id": dataset_ref, "top_n": top_n,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_pseudotime(*, dataset_ref: str, root_marker: str = "") -> dict:
        """扩散伪时序（Phase 32）：diffmap+DPT → 伪时序/轨迹图。"""
        try:
            out = runner.run("pseudotime", {
                "dataset_id": dataset_ref, "root_marker": root_marker,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_de(*, dataset_ref: str, groupby: str, group_a: str,
              group_b: str, method: str = "wilcoxon", top_n: int = 20) -> dict:
        """组间差异（Phase 33）：两组定向 DE → csv+火山图。"""
        try:
            out = runner.run("de", {
                "dataset_id": dataset_ref, "groupby": groupby,
                "group_a": group_a, "group_b": group_b,
                "method": method, "top_n": top_n,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_subcluster(*, dataset_ref: str, clusters: list[str],
                      n_top_hvg: int = 2000, n_pcs: int = 50,
                      n_neighbors: int = 15,
                      resolution: float = 1.0) -> dict:
        """亚聚类（Phase 33）：指定簇子集重聚类 → 新 dataset_ref。"""
        try:
            out = runner.run("subcluster", {
                "dataset_id": dataset_ref, "clusters": clusters,
                "n_top_hvg": n_top_hvg, "n_pcs": n_pcs,
                "n_neighbors": n_neighbors, "resolution": resolution,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_integrate(*, dataset_ref: str, batch: str,
                     method: str = "bbknn", n_top_hvg: int = 2000,
                     n_pcs: int = 50, n_neighbors: int = 15,
                     resolution: float = 1.0) -> dict:
        """批次整合（Phase 33）：bbknn → 新 dataset_ref。"""
        try:
            out = runner.run("integrate", {
                "dataset_id": dataset_ref, "batch": batch,
                "method": method, "n_top_hvg": n_top_hvg,
                "n_pcs": n_pcs, "n_neighbors": n_neighbors,
                "resolution": resolution,
            })
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out

    def sc_cellfreq(*, dataset_ref: str, by: str, group: str = "",
                    celltype_col: str = "leiden") -> dict:
        """组成比较（Phase 33）：比例表+卡方 → csv+堆叠图。"""
        try:
            out = runner.run("cellfreq", {
                "dataset_id": dataset_ref, "by": by, "group": group,
                "celltype_col": celltype_col,
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
                              "enum": ["hallmark", "go_bp", "kegg"]},
                    "default": ["hallmark", "go_bp", "kegg"]},
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
            "代谢通路活性分析（Phase 32，对齐 scMetabolism）：基于 KEGG 2021 "
            "通路库（镜像内置，离线）逐通路单细胞打分，输出代谢活性全矩阵 csv、"
            "簇×通路均值 csv、簇间方差 top_n 通路热图与 top 通路 UMAP 图。"
            "比较各簇代谢重编程（糖酵解/OXPHOS 等）首选。需先跑 sc_process。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string",
                                "description": "sc_process 输出的 dataset_ref"},
                "top_n": {"type": "integer", "default": 30, "maximum": 100,
                          "description": "返回/绘图的高方差通路数"},
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
