# 飞书科研闭环 Agent — Phase 4 MVP 设计稿

> 日期：2026-08-09
> 状态：Phase 4 MVP 设计定稿（待实施）
> 范围：仅 NCBI BLAST 工具 + 必要脚手架（注册到 ToolRegistry + process_phase4 入口）
> 推迟：富文本块 / 模板市场 / 工具热加载 / 文件夹批量上传（全部 Phase 5）
> 前置：[Phase 1](./2026-08-08-feishu-research-agent-design.md) / [Phase 2](./2026-08-08-feishu-research-agent-phase2-design.md) / [Phase 3](./2026-08-09-feishu-research-agent-phase3-design.md)
> 后置：Phase 4 MVP 实施计划 [`../plans/2026-08-09-feishu-research-agent-phase4-mvp.md`](../plans/2026-08-09-feishu-research-agent-phase4-mvp.md)（待写）

---

## 1. 范围与目标

### 1.1 Phase 4 MVP 主题

让 Agent **真做一次科研**：用户发"blast 搜索 BRCA1 人类蛋白"，Agent 自动调 NCBI BLAST，返回 top5 hits 写入飞书文档。打通"LLM → Planner → Executor → BLAST → 文档"全链路。

### 1.2 范围

| 子系统 | Phase 4 MVP 内容 | Phase 4 不做（推迟到 Phase 5）|
|---|---|---|
| **BLAST 工具** | NCBI Entrez web API（esearch + efetch）| 本地 BLAST+ / 多序列比对 |
| 脚手架 | 注册到 ToolRegistry + process_phase4 入口 | 富文本块 / 模板市场 / 工具热加载 / 文件夹批量上传 |

### 1.3 关键决策

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| 1 | 范围 | 仅 BLAST + 脚手架 | 用户要求最轻量 |
| 2 | BLAST 实现 | NCBI Entrez web API | 无需本地 BLAST+ 二进制 |
| 3 | BLAST API 调用 | esearch（取 ID）→ efetch（取记录）| 标准 NCBI 流程 |
| 4 | 限流策略 | 3 req/s token bucket | NCBI 官方限制 |
| 5 | BLAST 工具等级 | L0_read | 无副作用，无需审批 |
| 6 | BLAST 输入 | query (str) + database (str, 默认 "nr") + max_hits (int, 默认 5) | 最小可用 |
| 7 | BLAST 输出 | {"ids": [...], "records": [{"id", "title", "summary"}]} | 飞书文档能直接展示 |
| 8 | 工具注册 | ToolRegistry 内置（init 时注册）| Phase 5 之前不做热加载 |
| 9 | 模板渲染 | 复用 Phase 2 render_plan_summary | Phase 5 之前不做富文本 |
| 10 | Orchestrator 入口 | 新增 process_phase4 | Phase 3 process_phase3 保留 |

### 1.4 关键约束（继承 Phase 1-3）

- BLAST 工具继承 ToolHandler 完整流程（ASTGuard P0 拦截；query 字段不进代码）
- 失败重试复用 Phase 2 max_retries 配置
- 限流失败应清晰提示用户（"BLAST API 限流，请稍后"）
- BLAST 不修改任何文件、doc → L0_read 不需审批
- Plan 仍由 Planner（Phase 3）生成；BLAST 仅是 ToolRegistry 中一个 tool_name

---

## 2. 总体架构

```
FastAPI
 └─ Orchestrator.process_phase4(incoming)
   ├─ /bind-doc-renew 指令（同 Phase 3）
   └─ 普通消息
       ├─ Planner（Phase 3）→ DAGPlan（含 blast_search 节点）
       ├─ Scheduler（Phase 3）→ 提交任务
       ├─ LocalExecutor → ToolHandler.execute("blast_search", inputs)
       │  └─ BlastNCBITool.handle(query, database, max_hits)
       │     ├─ RateLimiter（3 req/s token bucket）
       │     ├─ esearch → 获取 hits IDs
       │     ├─ efetch → 获取 records
       │     └─ ToolResult.outputs = {"ids": [...], "records": [...]}
       ├─ TemplateEngine.render_plan_summary()（Phase 2）
       └─ DocAdapter.append_blocks()（Phase 1，仅 text block）
```

**与 Phase 3 的差异**：仅新增 BlastNCBITool + 注册到 registry + Orchestrator 入口换 process_phase4。

---

## 3. BLAST 工具设计

### 3.1 目标

让 Agent 通过自然语言触发 BLAST 搜索，返回 top N hits。

### 3.2 NCBI Entrez API

| 步骤 | HTTP | 端点 | 用途 |
|---|---|---|---|
| 1 | POST | https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi | 提交查询，返回 hits IDs |
| 2 | GET | https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi | 取 records（summary） |

**esearch**：
```
POST esearch.fcgi
  db=<database>&term=<query>&retmax=<max_hits>&retmode=json
```

**efetch**：
```
GET efetch.fcgi
  db=<database>&id=<id1>,<id2>,...&rettype=docsum&retmode=json
```

### 3.3 输入参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| query | str | ✓ | - | 搜索词（如 "BRCA1[Gene] AND Homo sapiens[Organism]"）|
| database | str | ✗ | "nr" | NCBI 数据库名 |
| max_hits | int | ✗ | 5 | 返回 hit 数（1-100） |

### 3.4 输出格式

```python
{
    "ids": ["12345678", "87654321", ...],
    "records": [
        {
            "id": "12345678",
            "title": "BRCA1 protein [Homo sapiens]",
            "summary": "This gene encodes a protein...",
            "length": 1863,
        },
        ...
    ],
    "query": "BRCA1[Gene] AND Homo sapiens[Organism]",
    "database": "nr",
    "total_count": 1234,   # NCBI 返回的总 hit 数
}
```

### 3.5 限流策略

```python
class RateLimiter:
    """3 req/s token bucket."""
    def __init__(self, rate=3, per_sec=1.0):
        self.rate = rate
        self.interval = per_sec / rate
        self._last_call = 0.0
    
    def wait(self):
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_call = time.monotonic()
```

### 3.6 错误处理

| 错误 | 返回 |
|---|---|
| NCBI 返回非 200 | ToolResult.error_code="BLAST_API_ERROR" |
| NCBI 返回无 hits | ToolResult.outputs={"ids": [], "records": [], "total_count": 0} |
| 限流超时（>5s）| ToolResult.error_code="BLAST_RATE_LIMIT_TIMEOUT" |
| query 为空 | ToolResult.error_code="BLAST_INVALID_QUERY" |
| 网络超时 | ToolResult.error_code="BLAST_TIMEOUT"（Phase 2 max_retries 自动重试）|

### 3.7 接口

```python
class BlastNCBITool:
    def __init__(self, *, rate_limiter=None, timeout_sec=30):
        self.rate_limiter = rate_limiter or RateLimiter(rate=3, per_sec=1.0)
        self.timeout_sec = timeout_sec
    
    def handle(self, *, query: str, database: str = "nr",
               max_hits: int = 5) -> dict:
        """执行 BLAST 搜索；返回 dict 给 ToolHandler 包装成 ToolResult。"""
        if not query or not query.strip():
            return {"error_code": "BLAST_INVALID_QUERY", "error_message": "query is empty"}
        try:
            ids, total_count = self._esearch(query=query, database=database,
                                              max_hits=max_hits)
        except httpx.HTTPError as e:
            return {"error_code": "BLAST_API_ERROR", "error_message": str(e)}
        if not ids:
            return {"ids": [], "records": [], "total_count": total_count,
                    "query": query, "database": database}
        try:
            records = self._efetch(ids=ids, database=database)
        except httpx.HTTPError as e:
            return {"error_code": "BLAST_API_ERROR", "error_message": str(e)}
        return {"ids": ids, "records": records,
                "total_count": total_count,
                "query": query, "database": database}
    
    def _esearch(self, *, query, database, max_hits) -> tuple[list[str], int]:
        self.rate_limiter.wait()
        resp = httpx.post(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            data={"db": database, "term": query,
                  "retmax": str(max_hits), "retmode": "json"},
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()
        ids = data.get("esearchresult", {}).get("idlist", [])
        total_count = int(data.get("esearchresult", {}).get("count", 0))
        return ids, total_count
    
    def _efetch(self, *, ids, database) -> list[dict]:
        self.rate_limiter.wait()
        resp = httpx.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": database, "id": ",".join(ids),
                    "rettype": "docsum", "retmode": "json"},
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()
        records = []
        for r in data.get("result", {}).values() if isinstance(
                data.get("result"), dict) else []:
            if isinstance(r, dict):
                records.append({
                    "id": r.get("uid", ""),
                    "title": r.get("title", ""),
                    "summary": r.get("summary", ""),
                    "length": r.get("length", 0),
                })
        return records
```

### 3.8 工具注册

```python
# orchestrator/tools/builtin/l3_bio.py
from orchestrator.tools.tool_registry import ToolRegistry, ToolSpec
from orchestrator.tools.bio.blast_ncbi import BlastNCBITool

def register_l3_bio(registry: ToolRegistry):
    """注册 L3 领域工具（BLAST 等）。Phase 4 MVP 仅含 BLAST。"""
    blast = BlastNCBITool()
    registry.register(ToolSpec(
        name="blast_search",
        description="BLAST 搜索 NCBI 数据库。输入：query, database, max_hits。返回 hits IDs 与摘要。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索词"},
                "database": {"type": "string", "default": "nr",
                             "description": "NCBI 数据库名（如 nr, protein, nucleotide）"},
                "max_hits": {"type": "integer", "default": 5, "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
        risk_level="L0_read",
        handler=lambda **inputs: blast.handle(**inputs),
    ))
```

### 3.9 配置

```python
# config/settings.py 新增
blast_api_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
blast_rate_per_sec: float = 3.0
blast_timeout_sec: int = 30
blast_max_hits_default: int = 5
```

---

## 4. Orchestrator 集成（process_phase4）

### 4.1 目标

最小改动：Orchestrator 注入 L3_bio 工具，新增 `process_phase4()` 入口。

### 4.2 init 变化

```python
# orchestrator/app.py
def __init__(self, ..., settings=None, ...):
    ...
    if settings is not None and doc_adapter is not None and ...:
        # Phase 2 注册
        register_l0_read(...)
        register_l1_compute(...)
        register_l2_side_effect(...)
        # Phase 4 MVP 新增
        from orchestrator.tools.builtin.l3_bio import register_l3_bio
        register_l3_bio(self.registry)
        ...
```

### 4.3 process_phase4 入口

```python
def process_phase4(self, incoming: IncomingMessage) -> dict:
    """Phase 4 MVP：复用 process_phase3 + 自动注册 BLAST。

    与 process_phase3 的区别：
    - ToolRegistry 内置 blast_search 工具
    - Planner 可生成包含 blast_search 节点的 DAG
    """
    # /bind-doc-renew 指令（继承 Phase 3）
    if incoming.text.strip() == "/bind-doc-renew":
        return self._handle_renew(incoming)
    # 普通消息：委托给 process_phase3 路径
    return self.process_phase3(incoming)
```

**Phase 4 MVP 简化**：直接复用 process_phase3，无需新增复杂流程；区别仅在 ToolRegistry 中多注册了一个工具。

---

## 5. 测试策略

### 5.1 测试矩阵

| 子系统 | 单元 | 集成 | 端到端 |
|---|---|---|---|
| RateLimiter | 2 | - | - |
| BlastNCBITool._esearch | 2 | - | - |
| BlastNCBITool._efetch | 1 | - | - |
| BlastNCBITool.handle | 4 | - | - |
| L3_bio 注册 | - | 1 | - |
| Orchestrator.process_phase4 | - | - | 1 |
| **合计** | **9** | **1** | **1** |

### 5.2 E1-E3 端到端场景

| # | 场景 | 验证点 |
|---|---|---|
| E1 | "blast 搜索 BRCA1 人类蛋白" mock LLM + mock API | DAG 含 blast_search → BLAST 返回 → 文档写入 |
| E2 | BLAST API 限流 | 连续 5 次请求，最后一次等待时间 ≥ 1s |
| E3 | BLAST API 错误返回 | NCBI 返回 500 → ToolResult.error_code="BLAST_API_ERROR" |

### 5.3 mock 策略

- **HTTPX mock**：respx 拦截 esearch.fcgi / efetch.fcgi
- **LLM mock**：FakeLLMRouter 返回含 blast_search 的 DAG
- **RateLimiter 测试**：直接调 `wait()` 测间隔

### 5.4 覆盖率目标

| 模块 | 目标 |
|---|---|
| `orchestrator/tools/bio/blast_ncbi.py` | ≥ 85% |
| `orchestrator/tools/builtin/l3_bio.py` | ≥ 80% |
| **Phase 4 新增整体** | ≥ 80% |

### 5.5 回归保证

- Phase 3 全部 216 测试**必须**继续通过
- Phase 1-2 全部测试**必须**继续通过
- L0/L1/L2 工具注册无副作用

---

## 6. Phase 4 MVP 不做（明确边界）

| 项 | 推迟到 | 原因 |
|---|---|---|
| 富文本块（heading/code/quote/table/list/image）| Phase 5 | MVP 不需要 |
| 模板市场（Block + sub-Plan）| Phase 5 | MVP 使用 Phase 2 内置 template |
| 工具热加载 | Phase 5 | 内置 BLAST 已够用 |
| 文件夹批量上传 | Phase 5 | MVP 范围聚焦 BLAST |
| 本地 BLAST+ 二进制 | Phase 5 | NCBI web API 够用 |
| 多序列比对 | Phase 5 | 单查询 BLAST 已够用 |
| AlphaFold | Phase 5 | 与 BLAST 独立 |
| 7 项 Phase 3 增强（A-G）| Phase 5 | 维持 Phase 4 范围聚焦 |
| BLAST 结果缓存 | Phase 5 | NCBI 重复请求风险 |
| BLAST 高级选项（e.g. 物种过滤）| Phase 5 | MVP 默认参数已够 |
| BLAST 异步并行 | Phase 5 | MVP 串行足够 |

---

## 7. 后续动作

**Phase 4 MVP 实施前**：

1. ✅ **写完本 spec**：7 章全部展开
2. ⏭️ **用户评审 spec**
3. ⏭️ **调用 writing-plans skill**：把 spec 拆成 5-8 个 Task 的实施计划
4. ⏭️ **用户评审 plan**
5. ⏭️ **Inline 实施**（沿用 Phase 3 节奏）

**Phase 5 候选范围**（基于 Phase 4 MVP 完成度）：

| 模块 | Phase 5 候选 |
|---|---|
| 富文本块 | 6 类 block + 飞书 doc 渲染 |
| 模板市场 | Block + sub-Plan + 用户上传 |
| 工具热加载 | 管理员上传 + AST check |
| 文件夹批量上传 | 递归 + 进度 + 权限 |
| 领域工具 | 本地 BLAST+ / AlphaFold / 多序列比对 |
| Phase 3 增强 | while/for 嵌套 / break/continue / 自动重试 / 总结失真检测 / 选择性遗忘 / symlink / 群聊合签 |

**Phase 4 MVP 阶段门**：

| 阶段门 | 标准 |
|---|---|
| 设计门 | 本 spec 用户确认 |
| 实施门 | plan 用户评审通过 |
| 测试门 | 9 新测试全部通过；216 老测试 0 回归 |
| 演示门 | 真 NCBI API + "blast BRCA1" 全链路跑通 |

---

## 8. 实施结果（已交付）

**commit**：`63cbf0f feat(phase4-mvp): implement NCBI BLAST tool`

### 实际测试数

| 模块 | 计划测试数 | 实际测试数 |
|---|---|---|
| Settings (BLAST) | 1 | **1** |
| RateLimiter | 2 | **2** |
| BlastNCBITool | 5 | **5** |
| L3_bio 注册 | 2 | **2** |
| Orchestrator smoke | 1 | **1** |
| E1-E3 e2e | 3 | **3** |
| **新增小计** | 14 | **14** |
| Phase 3 累计 | 216 | 216 |
| **总计** | **230** | **230** ✅ |

### 累计测试数

```
Phase 1:    86  → Phase 2:   147  → Phase 3:   216  → Phase 4 MVP: 230
            +61            +69            +14
```

### 新增/修改文件清单

```
新增：
- orchestrator/tools/bio/__init__.py
- orchestrator/tools/bio/rate_limiter.py              # 3 req/s token bucket
- orchestrator/tools/bio/blast_ncbi.py                # NCBI Entrez API
- orchestrator/tools/builtin/l3_bio.py                # register_l3_bio
- tests/unit/test_rate_limiter.py
- tests/unit/test_blast_ncbi.py
- tests/unit/test_l3_bio_registry.py
- tests/unit/test_orchestrator_phase4.py
- tests/unit/test_phase4_settings.py
- tests/integration/test_e2e_phase4_blast.py

修改：
- config/settings.py                                  # +4 blast_* 字段
- orchestrator/app.py                                 # register_l3_bio + process_phase4
- orchestrator/tools/tool_handler.py                  # 透传 handler error_code
```

### 用户可触发场景（演示门）

```
用户: "blast 搜索 BRCA1 人类蛋白"
  ↓ Planner（Phase 3 LLM）
DAG: blast_search(query="BRCA1 AND Homo sapiens", database="nr", max_hits=5)
  ↓ Scheduler（Phase 3）
  ↓ LocalExecutor → ToolHandler
  ↓ BlastNCBITool
    ├─ RateLimiter.wait() (3 req/s)
    ├─ esearch.fcgi  → 取 top 5 IDs
    ├─ efetch.fcgi   → 取 records
    └─ ToolResult.outputs = {ids, records, total_count, query, database}
  ↓ TemplateEngine.render_plan_summary()
  ↓ DocAdapter.append_blocks() (Phase 1)
飞书文档：含 5 个 BLAST hits（ID + title + summary）
```

### 风险记录（已实施，未触发）

| 风险 | 实际验证结果 |
|---|---|
| NCBI 限流违反 3 req/s | E2 测试：RateLimiter 强制 0.333s 间隔 ✅ |
| API 返回 500 | E3 测试：返回 `BLAST_API_ERROR` 错误码 ✅ |
| query 为空 | test_handle_invalid_query_returns_error ✅ |
| no hits | test_handle_returns_empty_when_no_hits ✅ |
| L0_read 工具无审批 | ToolHandler.execute() 直接调 handler ✅ |
| 注册副作用 | 216 + 14 = 230 测试通过，0 回归 ✅ |