# 飞书科研闭环 Agent（feishu-agent-scstanalysis）

> A research agent that lives in Feishu (Lark) IM: chat in, single-cell & spatial transcriptomics reports out — LLM-planned DAGs executed in offline Docker sandboxes, results written back to Feishu docs, cards and Bases.

[![CI](https://github.com/lkp101ksp-spec/feishu-agent-scstanalysis/actions/workflows/ci.yml/badge.svg)](https://github.com/lkp101ksp-spec/feishu-agent-scstanalysis/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/lkp101ksp-spec/feishu-agent-scstanalysis)](https://github.com/lkp101ksp-spec/feishu-agent-scstanalysis/releases)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![mypy](https://img.shields.io/badge/mypy-strict-brightgreen)
![ruff](https://img.shields.io/badge/lint-ruff-orange)

## 这是什么

一个以**飞书 IM 为前端**的科研分析 Agent。用户在飞书里发一句话（如 `/research 分析这份单细胞数据`，或直接私聊描述需求），后端自动完成：

```
飞书消息 → LLM 规划任务 DAG → 断网 Docker 沙箱执行分析 → 结果写回飞书（文档/卡片/多维表格）
```

覆盖**单细胞转录组（scRNA-seq）**与**空间转录组（Visium 等）**两条完整分析链，内置 **61 个工具**、**30+ Phase 的真机验收沉淀**，从质控、聚类、注释一路到细胞通讯、反卷积、轨迹推断、代谢与通路活性打分，最终产出带 LLM 中文解读的 structured 研究报告。

## 核心能力一览

| 方向 | 能力 |
|---|---|
| 单细胞分析 | 质控 / 双联体 / 归一化聚类 / marker / 细胞类型自动注释（celltypist）/ 批次整合（bbknn·harmony）/ 亚群重聚类 / WNN 多组学 |
| 空间转录组 | 空间域识别 / 空间统计 / cell2location 反卷积 / 生态位（niche）重构与邻域分层 / 空间轨迹 |
| 细胞通讯 | CellChat v2（R 桥）/ NicheNet / liana / COMMOT（空间方向性）/ MISTy 多视图建模 |
| 功能打分 | PROGENy 14 通路活性（MLM）/ KEGG 代谢（AUCell·mean 双口径）/ CytoSig 细胞因子信号 / 自定义基因集打分 |
| 组成与丰度 | 细胞频率（Fisher·Ro-e·共现网络）/ Milo 差异丰度 / 细胞周期 |
| 调控与扰动 | pySCENIC 调控网络 / scTenifoldKnk 虚拟敲除 / CollecTRI TF 调控子 |
| 轨迹推断 | 伪时序 / CytoTRACE2 / Monocle3 / slingshot / Palantir |
| 基因组层面 | inferCNV（sc + st）/ TCR 免疫组库（Startrac）/ bulk 解卷积 |
| 报告生成 | 章节骨架 + LLM 逐节中文解读（数字禁编造）→ 飞书文档 + 进度卡 + Base 投影 |
| 飞书协作 | 审批卡（高风险写操作人工放行）/ 评论闭环 / 模板系统 / 意图识别自动转研究任务 |
| Coding Agent | `/code` 指令进入自研 function-calling 循环，支持 skill 热加载与失败自诊断 |
| 自进化（RSI-L1） | 失败自动诊断写回 skill（Phase 27-29）+ 成功复盘沉淀新 skill（Phase 77）；双入口：自动复盘 + `/skill install` zip 即装；全程 human-in-loop 审批 |

## 架构

### 数据流

```
飞书 IM ──webhook──┐
                  ├→ gateway（验签/限流/幂等）→ orchestrator
飞书 IM ──ws 长连接─┘   （ADR-0031，双通道同一管线）
                            │
                            ├─ Planner：LLM 生成任务 DAG
                            ├─ Scheduler/ToolHandler：逐节点分发
                            │     └─ BioRunner：断网短命容器（--network none，资源限额）
                            │           ├─ bio 镜像 → sandbox/sc_tools（38 个脚本）
                            │           └─ st 镜像  → sandbox/st_tools（15 个脚本）
                            ├─ report_builder：产物汇编 + LLM 逐节解读
                            └─ feishu_adapter：IM/文档/卡片/Base 写回
                                     │
                            PostgreSQL（session/task/审批/模板/审计，20 个 repository）
```

### 顶层结构

| 目录 | 职责 |
|---|---|
| `gateway/` | 飞书事件入口：FastAPI webhook + WebSocket 长连接双通道 |
| `orchestrator/` | 编排核心：planner（DAG）/ executor / tools（61 内置工具）/ report / templates / coding |
| `sandbox/` | 三个容器镜像（bio / st / kernel）+ 容器内分析脚本（sc_tools·st_tools·r_tools） |
| `persistence/` | SQLAlchemy + Alembic，20 个 repository |
| `feishu_adapter/` | 飞书 OpenAPI 封装：im / doc / drive / base / comment / bot_info |
| `shared/` | schemas / errors / enums 共享契约 |
| `docs/` | 34 份 ADR + 80+ 份 design/plan 文档 + ROADMAP |

## 61 个内置工具

按风险分级：L0 只读 / L1 纯计算 / L2 飞书写副作用 / L3 领域分析。

**L0×3**：`read_doc` `read_base` `list_drive`
**L1×3**：`summarize_text` `classify_intent` `run_python`
**L2×4**：`write_doc` `write_base_projection` `send_card` `upload_drive`（均带审批闸）
**L3×1（生信通用）**：`blast_search`

### L3 单细胞 sc_*（30 个）

| 类别 | 工具 |
|---|---|
| 数据与质控 | `sc_load` `sc_qc` `sc_process` `sc_doublet` `sc_meta` |
| 注释与整合 | `sc_annotate` `sc_integrate` `sc_subcluster` `sc_wnn` |
| 差异与可视化 | `sc_de` `sc_markers` `sc_plot` `sc_enrichment` |
| 功能打分 | `sc_score_genes` `sc_genescore` `sc_metabolism` |
| 组成分析 | `sc_cellfreq` `sc_milo` `sc_cellcycle` |
| 通讯与信号 | `sc_cellchat` `sc_cellchat_v2` `sc_nichenet` `sc_cytosig` |
| 轨迹 | `sc_pseudotime` `sc_cytotrace2` |
| 调控与扰动 | `sc_scenic` `sc_knockout` |
| 其他 | `sc_tcr` `sc_cnv` `sc_deconv` |

### L3 空间转录组 st_*（20 个）

| 类别 | 工具 |
|---|---|
| 基础 | `st_load` `st_qc` `st_process` `st_markers` `st_plot` |
| 空间域与统计 | `st_domains` `st_stats` `st_integrate` |
| 反卷积与生态位 | `st_deconvolve` `st_niche` `st_niche_scan` `st_vicinity` |
| 通讯 | `st_commot` `st_cellchat_v2` `st_nichenet` `st_misty` |
| 其他分析 | `st_cnv` `st_trajectory` `st_genescore` `st_metabolism` |

另有 `/code` 链路 6 个 coding 原语（read/write/edit/list/search/run_cmd）+ 可热加载 skill 系统。

系统的自进化能力：`/code` 失败时 SkillDiagnoser 自动分析轨迹生成改进卡（审批后写回 skill）；成功时 SkillDistiller 复盘重复模式提议沉淀新 skill；也可 `/skill install` 附 zip 手动安装。所有写回均经审批卡 + .bak 兜底 + 审计（skill_improve_id 全链可追溯）。

## 沙箱镜像

| 镜像 | 内容 |
|---|---|
| `bio:cpu-latest` | scanpy 全家桶：anndata/scanpy/gseapy/bbknn/liana/celltypist/scrublet/pyscenic/muon + R 4.5 桥（CellChat/CytoTRACE2/Monocle3/slingshot/NicheNet/scTenifoldKnk） |
| `bio:gpu-latest` | rapids-singlecell GPU 变体（93665 细胞全链 5m46s 实测） |
| `bio:st-cpu-latest` | 空间栈：squidpy/commot/torch+cell2location/infercnvpy/liana/decoupler（PROGENy·CollecTRI 快照） |

所有分析容器：**断网（`--network none`）、短命、stdin 传参、产物落盘 workspace、dataset_id 幂等**。

## 快速开始

### 前置要求

- Python 3.10+（推荐 3.12）、Docker、PostgreSQL 16（或 `docker compose up -d postgres`）
- 飞书企业自建应用（App ID / Secret，开通 IM·文档·多维表格权限）
- 至少一个 LLM provider key（DeepSeek / Qwen 等，config/llm.yaml 可配主备路由）

### 安装与启动

```bash
# 1. 依赖（锁文件钉版，本机/CI 一致）
pip install -r requirements-lock.txt
pip install -e . --no-deps

# 2. 配置
cp .env.example .env   # 填入飞书凭据与 LLM key
python scripts/config_check.py --env-file .env

# 3. 数据库
docker compose up -d postgres
alembic upgrade head

# 4. 沙箱镜像
docker build -f sandbox/bio.Dockerfile -t feishu-research-agent/bio:cpu-latest .
docker build -f sandbox/st.Dockerfile  -t feishu-research-agent/bio:st-cpu-latest .

# 5. 启动（二选一或并存）
python -m gateway.ws_client                                          # WebSocket 长连接（推荐，零公网入口）
uvicorn gateway.app:create_app --factory --host 0.0.0.0 --port 8000  # webhook 模式
```

然后在飞书里给机器人发 `/research 你的分析需求` 即可。

### 质量门（开发）

```powershell
powershell scripts\check.ps1   # ruff → mypy strict → pytest + cov（pre-push 自动执行）
```

## 工程化

- **测试**：unit + integration + pg 真库三层，当前 **1362 passed**；单测 300s 超时防挂死
- **静态检查**：mypy 全库 strict（~204 文件）+ ruff；Windows 开发机额外跑 `mypy --platform linux` 反模拟消平台盲区
- **CI**：4 个 job——test（Postgres 服务容器全层）/ docker-smoke（真容器集成）/ bio-image-smoke·st-image-smoke（手动触发，14 个断网合成冒烟）
- **依赖纪律（ADR-0028）**：`requirements-lock.txt` 全量钉版，本机/CI 共用一份
- **文档文化**：34 份 ADR + 80+ 份 superpowers design/plan，所有架构决策可追溯（`docs/`）

## 文档导航

| 入口 | 内容 |
|---|---|
| `docs/ROADMAP.md` | Phase 15+ 全部里程碑与真机验收记录 |
| `docs/adr/` | 34 份架构决策记录（ADR） |
| `docs/superpowers/specs/` | 各 Phase 设计 spec（brainstorming → spec → plan → 实施的工作流产物） |
| `测试总结+*.md` | 逐次测试实录与挂账追踪 |

## 许可与声明

- 本仓库为科研工程实践代码，**暂未指定开源许可证**（保留所有权利）；仅供学习参考。
- 分析容器断网运行，数据不出本机；LLM 调用仅传输任务编排所需的文本上下文。
- 飞书凭据、LLM key 等敏感信息一律走 `.env`（已 gitignore），仓库内不存在任何真实凭据。
