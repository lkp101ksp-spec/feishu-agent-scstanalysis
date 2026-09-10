# D 分析报告自动汇编 设计 spec

状态：已批准（2026-09-10 对话）；已实施

## 0. 背景与决策链

生信扩展四选二（2026-09-10 对话）：B1 CNV 推断先行（已实施，见
2026-09-10-scnv-design.md），**D 报告汇编随后**；TCR 免疫组库砍掉，
GEO/TCGA 数据获取与 RNA velocity 不做。

本 spec 需求定案（同对话逐项拍板）：

1. **核心定位**：流程收尾自动出报告——单细胞流程跑到收尾时自动汇编，
   无需用户额外指令。不做"用户随叫随出"手动触发（YAGNI）。
2. **交付形态**：新建飞书云文档（结构化章节+插图，IM 发链接）+
   bio_workspace 留 Markdown 底稿（归档/调试/复用）。
3. **解读深度**：LLM 深度解读——逐节写生物学解读 + 末节总体结论，
   像生信分析师写的报告，不做纯模板拼装。
4. **架构方案**：方案甲——orchestrator 内建 `orchestrator/report/` 模块 +
   research_runner 收尾钩子。否掉方案乙（L3 工具靠 planner 自觉挂尾节点，
   触发不可靠）与方案丙（手动触发薄壳，YAGNI）。

## 1. 架构与触发

新增 `orchestrator/report/` 模块：

- `section_digest.py`：产物收集 + csv 摘要截断（纯函数，易测）
- `report_builder.py`：章节骨架、LLM 逐节解读、Markdown 拼装
- `__init__.py`：对外 `maybe_build_report(...)` 单入口

触发点：[research_runner.py](../../../orchestrator/research_runner.py)
收尾链 `_send_sc_images()` 之后、完成卡 `card.finish()` 之前。

**条件触发**：本次 plan 中 ≥1 个 `sc_*` 工具节点终态 SUCCESS 才汇编；
否则零动作（纯闲聊/联网研究流程不受任何影响）。

**故障隔离**：整个汇编包一层 try/except——失败记 warning 日志 +
IM 回复附一行"报告生成失败：{原因}"，任务终态与既有收尾链
（IM 摘要/发图/写回绑定文档）完全不受影响。

## 2. 章节骨架与产物收集

数据源：scheduler 节点 handle（`handle.state == ExecutionState.SUCCESS`
且 `handle.outputs` 非空），容器路径经
`_sc_image_host_paths()` 同款 `/ws` → 宿主 `bio_workspace` 翻译逻辑
转宿主路径（复用既有翻译，不另写一套）。

固定章节映射表（跑过什么出什么；顺序固定如下）：

| 章节 | 来源工具 | 数据 | 图 |
|---|---|---|---|
| 数据质控与预处理 | sc_process | emit 细胞/基因数、HVG 数 | umap.png |
| 双联体检测 | sc_doublet | emit + doublet_rate_by_cluster.csv | doublet_umap.png |
| 细胞类型注释 | sc_annotate | celltypist/markers 两路 csv | 注释 UMAP |
| 细胞周期 | sc_cellcycle | emit + csv | 其图 |
| 差异表达 | sc_de | de csv | volcano 图 |
| 细胞组成差异 | sc_cellfreq / sc_milo | 各自 csv | 各自图 |
| CNV 恶性判定 | sc_cnv | cnv_celltype_summary.csv + cnv_subclone_by_chromosome.csv + emit 关键数字 | 染色体热图/score UMAP/亚克隆 UMAP |
| 细胞通讯 | sc_cellchat | 通讯 csv | 网络图 |
| 空间反卷积 | sc_deconv | 比例 csv | 空间图 |
| 调控网络 | sc_scenic | rss.csv 等 | scenic_heatmap.png |
| 基因敲除 | sc_knockout | diffRegulation.csv | knockout_volcano.png |
| 总体结论 | 各节解读段落（非原始数据） | — | — |

每节三要素：模板化小节标题、产物图（云文档插图 + md 相对路径引用）、
LLM 解读段落。

csv 摘要截断规则：头部 30 行 + emit 关键数字；单节 LLM 输入
≤4000 字符（OBS_TRUNCATE 既有惯例），超出头 45% 尾 30% 保留。

## 3. LLM 解读策略

- 逐节一次 `llm.chat()`（research 场景 LLMRouter，scene_router 既有
  双绑）：system 词 = 生信分析师人设 + 该节工具名 + csv 摘要 + emit
  关键数字 → 输出 150-300 字中文解读，要求点出关键趋势、数字必须
  来自输入、不许编造。
- 末节"总体结论"再调一次：输入各节解读段落（非原始数据）→
  ≤300 字串烧结论。
- 预算护栏：单次报告 LLM 调用 ≤12 次（11 节上限 + 总评）。
- 单节 LLM 失败 → 降级模板句"本节展示 {工具} 分析结果，详见下图与
  附件数据"，**不中断**后续节；总评失败 → 省略该节。

## 4. 交付链路与降级

1. **Markdown 底稿必落盘**（最先做，任何后续失败都不丢内容）：
   `bio_workspace/{ds}/report/report_{YYYYMMDD_HHMMSS}.md`，
   图用相对路径引用。
2. feishu_adapter 新增封装（均落 DocAdapter——sdk_client 与
   `_sdk_request` 皆在该类，DriveAdapter 无 SDK 通道）：
   - `DocAdapter.create_document(title, folder_token=None)`
     （POST /open-apis/docx/v1/documents）→ 返回 doc_id
   - `DocAdapter.grant_doc_view(doc_id, open_id)`
     （POST /open-apis/drive/v1/permissions/{doc_id}/members?type=docx，
     member_type=openid，perm=view）
3. 链路：create_document → `render_blocks` 写章节块 → 图经既有
   `upload_doc_image`/`insert_doc_image` 插图 → `grant_view` 授权
   提问用户（`incoming.sender_open_id`，view 权限）→ IM reply 发
   文档链接。
4. folder_token：config 可配项（`report_folder_token`），缺省建应用
   根目录。
5. **降级链**：建文档或授权失败 → IM 改发报告要点摘要（总评段落 +
   章节清单）+ md 底稿宿主路径提示；md 底稿始终存在。

## 5. 测试与文件清单

### 测试

- 单测 `tests/unit/test_report_builder.py`（fake LLMRouter /
  IMAdapter / DocAdapter，照 tests/unit 既有 fake 模式）：
  1. 章节骨架构建：部分工具缺失时只出有数据的节、顺序正确
  2. csv 摘要截断：>4000 字符时头尾保留 + 总长合规
  3. LLM 单节失败 → 降级模板句且后续节照常
  4. md 拼装：标题/图片相对路径/各节齐全
  5. 建文档或授权失败 → 走降级 IM 文案，md 底稿仍在
  6. 无 sc_* SUCCESS 节点 → 完全不触发（不调 LLM、不建文档）
- 集成：用 B1 合成数据集产物 fixture 跑一次真实 md 底稿生成
  （不碰真飞书；feishu 侧无集成测试，与既有 feishu_adapter 一致——
  该适配器历来单测 mock HTTP）。
- research_runner 钩子：单测断言触发条件与异常隔离（汇编抛错时
  主流程返回不受影响）。

### 文件清单

| 文件 | 动作 |
|---|---|
| orchestrator/report/__init__.py | 新增（maybe_build_report 入口） |
| orchestrator/report/section_digest.py | 新增（产物收集+csv 摘要） |
| orchestrator/report/report_builder.py | 新增（骨架+LLM+md 拼装） |
| orchestrator/research_runner.py | 改（收尾钩子 ~20 行） |
| feishu_adapter/doc_adapter.py | 改（create_document + grant_doc_view） |
| config/settings.py | 改（report_folder_token 字段 + env） |
| tests/unit/test_report_builder.py | 新增 |
| docs/平台功能说明书.md | 改（D 功能描述） |
| docs/使用说明书.md | 改（报告自动产出说明） |
| docs/ROADMAP.md | 改（变更记录行，hash/CI 推送后回填） |
| 测试总结+2026-09-09T01-55-00.md | 改（追加七） |

### 门禁

mypy 双平台（win32 + --platform linux）0 错、ruff line-length 110 全绿、
pytest 全量绿（basetemp 指向 $env:TEMP 专用目录）。同 B1 标准。

## 6. 明确不做

- 不做用户手动触发"出报告"（YAGNI，定位已定流程收尾自动出）
- 不做 csv/数据文件的飞书文件消息回传（现状仅文本列路径，保持）
- 不做报告审批卡（新建文档+授权提问者本人 view 属低风险副作用，
  与 bind_scope 直写同级，不引入审批）
- 不做历史流程补报告（只对新跑的流程生效）
