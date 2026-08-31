# Phase 20：单细胞转录组分析（scRNA-seq）— 设计文档

- 日期：2026-09-01
- 状态：设计已与用户对齐（范围/数据入口/算力/交互模式四项决策 + 方案 A 认可），待评审
- 来源：用户需求（替代原 BLAST+/AlphaFold 方向）；ROADMAP Phase 20 改写

## 1. 已确认决策

| 决策点 | 结论 |
|---|---|
| 一期范围 | 仅 scRNA-seq 标准流程；空间转录组二期 |
| 数据入口 | 本地路径（用户 IM 里发路径，工具直读；路径白名单校验） |
| 算力 | CPU 先行（scanpy + leidenalg），镜像/接口按可替换设计预留 GPU |
| 交互模式 | 分步工具节点（sc_load/sc_qc/sc_process/sc_markers/sc_plot），planner 组 DAG |
| 容器架构 | 方案 A：独立短命 bio 容器 + 主机目录挂载（与研究沙箱解耦） |
| 结果输出 | PNG 图 IM 图片消息 + 摘要表写绑定文档（card_confirm 审批） |

## 2. 背景：为什么不复用现有沙箱

现有 run_python 沙箱（`DockerSandbox`）的设计假设与生信分析根本冲突：

| 维度 | run_python 沙箱 | scRNA-seq 分析 |
|---|---|---|
| 代码来源 | LLM 生成任意代码（不可信） | 参数化固定脚本（可信） |
| 文件系统 | read-only + 512MB tmpfs | GB 级 h5ad 读写 |
| 生命周期 | session 长驻复用 | 单次任务 |
| 依赖 | 轻量 harness | scanpy/anndata/leidenalg/matplotlib 重栈 |

混用会让重依赖拖慢研究沙箱冷启动，且 read-only/tmpfs 无法承载大文件。

## 3. 架构

```
IM "/research 分析 D:/data/pbmc.h5ad，标准流程"
  → planner（sc_* 工具 schema 可见）→ DAG:
      n1 sc_load(path)            → dataset_ref + 概要统计
      n2 sc_qc(n1.dataset_ref)    → 过滤前后统计
      n3 sc_process(n1.dataset_ref) → UMAP/Leiden 概要 + umap.png
      n4 sc_markers(n1.dataset_ref) → 每簇 top 基因 + dotplot.png
  → Runner 收尾：图 IM 发送 + 摘要写文档（card_confirm）
```

### 3.1 bio 镜像（sandbox/bio.Dockerfile）

- 基础：`python:3.12-slim`
- 依赖：scanpy、anndata、leidenalg、igraph、matplotlib、numpy、scipy、pandas、h5py（清华 pip 源）
- 固化脚本：`/opt/sc_tools/{load,qc,process,markers,plot}.py`（参数走 stdin JSON，结果 stdout JSON，图落 `/ws`）
- tag：`feishu-research-agent/bio:cpu-latest`（GPU 后续 `bio:gpu-latest`，settings 可切）

### 3.2 BioRunner（orchestrator/tools/bio/bio_runner.py）

```python
class BioRunner:
    def run(self, script: str, args: dict, *, timeout_sec: int = 900) -> dict:
        # docker run --rm --network none --cpus N --memory M
        #   -v <数据目录>:/data:ro  -v <bio_workspace_root>:/ws
        #   bio:cpu-latest python /opt/sc_tools/<script>.py
        # stdin=args JSON；stdout 解析 JSON；非 0 / 超时 → 工具级 error_code
```

- subprocess 直调（与 BlastLocalTool 同模式），同步阻塞在工具 handler 内
- 每次短命容器（`--rm`），无状态

### 3.3 dataset_ref：跨节点中间产物（核心机制）

- `dataset_id = sha1(绝对路径 + 文件大小)[:12]`——同一数据二次分析直接复用 workspace，无需重跑
- workspace 布局（bio_workspace_root，主机目录）：

```
<bio_workspace_root>/<dataset_id>/
  ├── raw.h5ad          # sc_load 产物（统一格式）
  ├── filtered.h5ad     # sc_qc 产物（过滤后；sc_process 优先读此文件）
  ├── processed.h5ad    # sc_process 产物（归一化+降维+聚类）
  ├── qc_stats.json     # sc_qc 概要
  ├── umap.png / dotplot.png / <gene>_violin.png
```

- sc_load 返回 `{"dataset_ref": "<dataset_id>", ...}`；下游工具参数 `dataset_ref` 由 planner 写 `n1.dataset_ref`，复用现有 scheduler 输入注入——**无需给 handler 传 task_id**（handler 只收 planner inputs，这是现有 ToolHandler 约束）

## 4. 工具契约（5 个，注册进 l3_bio.py，risk_level=L1_compute）

| 工具 | 输入（LLM 填） | 输出 | 镜像脚本 |
|---|---|---|---|
| sc_load | path（白名单内）、format（h5ad/mtx10x 自动探测） | dataset_ref、n_cells、n_genes、mt_pct 分布 | load.py |
| sc_qc | dataset_ref、min_genes(600)、min_cells(3)、max_mt_pct(20) | 过滤前后细胞/基因数、剔除数；产出 filtered.h5ad | qc.py |
| sc_process | dataset_ref、n_top_hvg(2000)、n_pcs(50)、n_neighbors(15)、resolution(1.0) | 簇数、每簇细胞数、silhouette、umap.png 路径 | process.py |
| sc_markers | dataset_ref、method(wilcoxon/t-test)、top_n(10) | 每簇 top 基因列表（JSON）、dotplot.png 路径 | markers.py |
| sc_plot | dataset_ref、genes[]、kind(violin/umap_gene) | png 路径列表 | plot.py |

- **输入文件回退链**：sc_process/sc_markers/sc_plot 优先读 `filtered.h5ad`（已跑 sc_qc），无则读 `raw.h5ad`（未跑 sc_qc 时 sc_process 内置默认过滤，支持 load→process 两步快速路径；sc_markers/sc_plot 无 processed.h5ad 时报错提示先跑 sc_process）
- 所有 sc_* `visible_to_planner=True`，加入 research 链路 visible 列表
- 工具内 timeout：BioRunner 900s（process 最重）

## 5. 安全

1. **路径白名单**（settings.bio_data_roots，逗号分隔）：sc_load 的 path `os.path.realpath` 后必须位于白名单目录内，否则 `SC_PATH_FORBIDDEN`——防 LLM 编造路径读敏感文件
2. 数据目录只读挂载（`-v ...:/data:ro`）
3. 容器 `--network none`（分析无需网络）+ cpu/memory 限制（settings 可配，默认 4C16G）
4. workspace 只挂 bio_workspace_root 单目录，脚本无任意路径能力（脚本内路径限定 /data、/ws 前缀）

## 6. 超时策略

- **节点级**：BioRunner subprocess timeout 900s（可配 bio_script_timeout_sec）
- **任务级**：research wall-clock 现默认 300s 不够。runner 改为：plan 含 sc_* 节点时 wall-clock 取 `max(research_task_timeout_sec, research_sc_timeout_sec=3600)`（新配置）；纯检索任务不受影响

## 7. IM 图片回传

- `im_adapter.upload_image(image_path) -> image_key`（新增，POST /open-apis/im/v1/images，image_type=message）
- Runner 收尾：sc 工具输出的 png 路径（workspace 内）→ upload_image → `send(chat_id, "image", {"image_key": ...})`，每图一条，上限 5 张防刷屏
- 文档写回：文本摘要（QC 统计表 + 簇构成 + top markers 表）走既有 blocks 生成 + card_confirm 审批；图片不进文档（docx 图片块受限，降级风险已知）

## 8. GPU 预留

- 镜像 tag 配置化（settings.bio_image）：后续换 rapids-singlecell 镜像 `bio:gpu-latest`（WSL2 + `--gpus all`），工具契约与 dataset_ref 机制不变
- 一期不做任何 GPU 代码

## 9. 测试计划

- [ ] BioRunner：命令拼装（挂载/网络/资源限制）、stdout JSON 解析、超时/非零退出错误码
- [ ] sc_load 路径白名单：白名单内通过、realpath 逃逸（`../`）拒绝、白名单外拒绝
- [ ] dataset_id：同路径同大小幂等；文件变化后变化
- [ ] 5 工具注册：schema/参数默认值/planner 可见
- [ ] runner 超时放大：plan 含 sc 节点用 3600，纯检索任务不变
- [ ] upload_image：SDK 调用参数与 image_key 解析（mock）
- [ ] 收尾发图：png 存在发送、上限截断、上传失败降级文本提示
- [ ] 集成：fake BioRunner 跑通 load→qc→process→markers DAG（dataset_ref 注入）
- [ ] 回归：全量测试通过

真机验收（推迟，与其他 Phase 合并）：
- [ ] pbmc3k 级数据一句话全流程：IM 收到 UMAP/dotplot 图 + 文档写入摘要
- [ ] 二次分析同数据不重跑 load
- [ ] 白名单外路径被拒的 IM 提示

## 10. 不做项

- 空间转录组（二期独立 spec）
- 自动细胞类型注释（SingleR/Azimuth/盲打分——二期）
- 轨迹/拟时序、CNV、通讯分析（远期池，按使用需求捞）
- 批次整合（harmony/BBKNN）、双细胞去除（DoubletFinder）
- 文档内嵌图片（docx image 块创建受限）
- 数据上传（云盘/IM 附件→本地，二期）

## 11. 风险

| 风险 | 缓解 |
|---|---|
| Docker Desktop 文件共享未启用导致 -v 失败 | 部署文档注明启用 I 盘共享；BioRunner 报错信息含 docker stderr |
| 大数据 process 超 900s | timeout 可配；真机验证后调；矩阵文件超大时提示用户预过滤 |
| matplotlib 中文字体缺失（图中文标签乱码） | 图内标签统一英文（基因名/cluster 编号天然英文） |
| dataset workspace 磁盘累积 | workspace 生命周期二期统一做（与 P16 产物回收合并）；一期手动清理 |

## 12. 交付物清单

- `sandbox/bio.Dockerfile` + `sandbox/sc_tools/*.py`（5 个脚本）
- `orchestrator/tools/bio/bio_runner.py`（BioRunner）
- `orchestrator/tools/bio/sc_analysis.py`（5 工具 handler + 注册函数 register_l3_singlecell）
- `orchestrator/runtime` 组装处挂 BioRunner（bio_image/bio_workspace_root/bio_data_roots/bio_script_timeout_sec 注入）
- `feishu_adapter/im_adapter.py` upload_image
- `orchestrator/research_runner.py` 超时放大 + 收尾发图
- `config/settings.py` 新配置 5 项
- 单测 + 集成测试
