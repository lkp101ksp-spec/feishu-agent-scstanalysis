# 飞书科研闭环 Agent — Phase 9 设计稿

> 日期：2026-08-26
> 状态：Phase 9 设计稿（已确认 A,A,A,A,A 方案）
> 范围：评论自动同步 + pending 摘要推送 + tsvector 统一检索 + diff 升级（hash + moved）
> 推迟：评论 webhook 实时推送（公网回调）/ 标签治理 / 模板 merge-branch / AlphaFold（Phase 10）
> 前置：[Phase 8](./2026-08-26-feishu-research-agent-phase8-design.md)（评论驱动闭环：sync + actions + diff + tags/favorites，445 测试）
> 后置：Phase 9 实施计划 [`../plans/2026-08-26-feishu-research-agent-phase9.md`](../plans/2026-08-26-feishu-research-agent-phase9.md)

---

## 1. 范围与目标

### 1.1 Phase 9 主题

**协作智能化**。Phase 8 闭合了"评论 → 动作"链路，但仍是手动两步（`/comments-sync` → `/comment-apply`），且检索入口割裂（trigram 与 by-tag 并存）、diff 存在重排序误报（ADR-0021 已知局限）。Phase 9 把手动闭环升级为**自动闭环**：后台轮询同步 → 发现待处理动作即推送 owner → 一条指令应用；同时统一检索入口、修复 diff 语义。

### 1.2 范围（4 子系统）

| 模块 | Phase 9 内容 | Phase 9 不做（推迟）|
|---|---|---|
| **评论自动同步** | 后台轮询 worker（asyncio 任务，间隔可配）；扫描 active session 的有效绑定 doc；逐 doc 复用 Phase 8 sync | webhook 实时推送 / 评论分页拉取 |
| **pending 摘要推送** | sync 后检测**新增**待处理动作评论 → IM 即时通知 owner（含摘要 + apply 提示）；notify 日志表去重防轰炸 | 定时日报 / 卡片交互式批量 approve |
| **tsvector 统一检索** | `search-v2` 单入口：全文（PG tsvector / SQLite LIKE 降级）+ 标签精确过滤 + 收藏数 boost 融合排序；旧接口保留 | ES / 拼音 / 标签推荐 |
| **diff 升级** | 内容 hash 精确配对 + `moved` 检测（v_a/v_b 内容相同仅位置变）；渲染加 `↔`；修复重排序误报 | LCS 全量 / 跨模板 diff |

### 1.3 关键约束（继承 Phase 1-8）

- **无公网回调**：本机部署条件不支持飞书事件订阅，自动同步走**轮询**（ADR-0024）
- **双库兼容**：tsvector 仅 PG 支持；SQLite（测试环境）自动降级 LIKE，按方言分支（ADR-0026）
- **旧接口零破坏**：`/templates/search`、`/templates/by-tag`、Phase 8 全部路由与指令语义不变
- **推送节制**：同一 comment 只通知一次（notify 日志表）；owner 已 apply 的不通知
- **diff 输出兼容**：`diff()` 返回结构仅**新增** `moved` 列表，`added/removed/changed` 语义不变（老测试零回归）

### 1.4 关键决策（用户已批：A,A,A,A,A）

| # | 决策点 | 选择 | ADR |
|---|---|---|---|
| 1 | Phase 9 范围 | A：协作智能化组合（4 子系统） | — |
| 2 | 自动同步机制 | A：后台定时轮询（asyncio，间隔可配） | 0024 |
| 3 | 推送时机 | A：sync 后即时推送 + notify 日志去重 | 0025 |
| 4 | 检索范围 | A：融合检索单入口（全文 + 标签 + 收藏 boost），旧接口保留 | 0026 |
| 5 | diff 升级 | A：内容 hash 配对 + moved 检测 | 0027 |

### 1.5 不做清单（明确边界）

- 飞书事件订阅 webhook（公网回调门槛）
- 评论分页拉取 / tombstone / admin 标签清理
- ES / 拼音检索 / 标签推荐 / 热门标签
- 模板 merge / branch / conflict / 跨 fork 同步
- AlphaFold / 多序列比对 / 本地 DB 自动同步
- Phase 2-8 推迟项

---

## 2. 总体架构

```
FastAPI（Phase 8 不变 + 2 新路由 + 可选 startup worker 钩子）
 ├─ startup: CommentAutoSyncWorker.start_async()      # 后台轮询（可选注入）
 │    └─ 每 interval 秒 tick()：
 │        list_active sessions（bound_doc 有效未过期）
 │        → CommentSyncService.sync(doc_id)            # Phase 8 复用
 │        → CommentNotifyService.notify_new_pending()  # 新增待处理 → IM 推 owner
 └─ Orchestrator.process_phase9(incoming)
   ├─ /template-find <q> [#tag] → UnifiedSearchService
   └─ 未匹配 → process_phase8 链（不变）

UnifiedSearchService.search(query, tag, scope)
 └─ TemplateRepo.search_v2（方言分支）
    ├─ PG: to_tsvector @@ plainto_tsquery + ts_rank + EXISTS(tag) + fav_count boost
    └─ SQLite: ilike(name)=2 / ilike(desc)=1 + EXISTS(tag) + fav_count boost
VersionDiffService._diff_lists v2
 └─ (type 分组 → 内容 hash 配对) → moved(index 变) / changed(余量按序) / added / removed
```

**新增模块**：

| 文件 | 职责 |
|---|---|
| `persistence/repositories/comment_notify_repo.py` | notify 日志表 CRUD（去重判重） |
| `orchestrator/templates/notify_service.py` | 新增 pending 检测 + IM 推送 + 打标 |
| `orchestrator/templates/auto_sync_worker.py` | 后台轮询（tick 单轮可测 + asyncio 循环） |
| `orchestrator/templates/unified_search_service.py` | 融合检索封装 |

**升级模块**：

| 文件 | 变更 |
|---|---|
| `persistence/models.py` | +`CommentNotifyRow`（16→17 表） |
| `persistence/repositories/template_repo.py` | +`search_v2()`（方言分支 + 融合排序） |
| `orchestrator/templates/diff_service.py` | `_diff_lists` 升级 hash 配对 + moved；render 加 `↔` |
| `config/settings.py` | +`comment_sync_interval_sec`（默认 300） |
| `gateway/app.py` | +2 路由（search-v2 / diff 不变已存在）+ startup worker 钩子 + 3 service 注入 |
| `orchestrator/app.py` | +`process_phase9()` + 1 IM 指令（/template-find） |

---

## 3. 评论自动同步（CommentAutoSyncWorker）

### 3.1 目标

消除手动 `/comments-sync`：后台定时扫描活跃绑定文档并同步评论，为推送子系统提供触发点。

### 3.2 数据流

```
worker.start_async()（gateway startup，可配开关）
  └─ 循环：sleep(interval) → tick()
       ├─ SessionRepo.list_active()
       │    过滤：bound_doc_id 非空 且 bind_expires_at > now
       ├─ 按 doc_id 去重（多 session 绑同一 doc 只 sync 一次）
       └─ 对每个 (doc_id, owner_open_id, chat_id)：
            CommentSyncService.sync(doc_id)          # Phase 8 幂等
            CommentNotifyService.notify_new_pending( # 见 §4
                doc_id=..., owner_open_id=..., chat_id=...)
```

### 3.3 设计要点

- **tick() 与循环分离**：`tick()` 是纯同步单轮（可单测），`start_async()` 只是 `while True: sleep; tick` 的薄壳——测试不依赖 asyncio 时序
- **异常隔离**：单个 doc sync 失败（网络/API）记日志继续下一 doc，不中断整轮
- **配置**：`Settings.comment_sync_interval_sec: int = 300`；`create_app(worker=None)` 不传则不启动（测试与旧部署零影响）
- **会话筛选**：`bind_expires_at` 已过期或为空的 session 跳过（沿用 Phase 1/3 绑定窗口语义）

---

## 4. pending 摘要推送（CommentNotifyService）

### 4.1 目标

owner 不用主动查：新增待处理动作评论到达（自动或手动 sync 后）即 IM 通知，附 apply 指令提示。

### 4.2 数据模型（CommentNotifyRow）

| 字段 | 类型 | 说明 |
|---|---|---|
| comment_id | String PK | 已通知的评论（天然去重键） |
| doc_id | String, index | 所属文档 |
| notified_at | DateTime | 通知时间 |

### 4.3 服务语义

```python
notify_new_pending(*, doc_id, owner_open_id, chat_id) -> dict:
    # 1. comment_repo.list_pending(doc_id)
    # 2. 过滤：parse_action(text) 非 None（动作评论）且 notify_repo.has(cid) 为 False
    # 3. 若空 → 返回 {"notified": 0}（不发消息防噪音）
    # 4. IM 推送到 chat_id：
    #    "[评论动作] doc <doc_id> 有 N 条待处理：
    #     - /replan t1 增加对照组（by 导师）
    #     发送 /comment-apply <doc_id> 应用"
    # 5. notify_repo.insert_many(命中 ids) → 返回 {"notified": N}
```

- **触发源**：auto_sync_worker.tick 每轮 sync 后调用；手动 `/comments-sync` 路径**不**自动推送（保持 Phase 8 语义，owner 主动行为无需提醒）
- **去重**：notify 日志表持久化；重启不重发；apply 后评论离开 pending，天然不再命中
- **权限提示**：消息中的 apply 指令由 owner 自己发（Phase 8 权限模型不变，ADR-0023）

---

## 5. tsvector 统一检索（UnifiedSearchService）

### 5.1 目标

单入口融合三维度：全文相关度（name+description）+ 标签精确过滤 + 收藏热度 boost。

### 5.2 方言分支（ADR-0026）

| 环境 | 全文实现 | 相关度 |
|---|---|---|
| PostgreSQL | `to_tsvector('simple', name || ' ' || description) @@ plainto_tsquery(:q)` | `ts_rank(...)` |
| SQLite（测试） | `name ILIKE %q% OR description ILIKE %q%` | name 命中=2 / 仅 desc 命中=1 |

统一融合排序（两方言共用表达式）：

```
final_score = text_score + LEAST(fav_count, 5) * 0.5
fav_count = (SELECT COUNT(*) FROM template_favorites f WHERE f.template_id = t.template_id)
ORDER BY final_score DESC, updated_at DESC
```

### 5.3 过滤条件

- `tag`：`EXISTS (SELECT 1 FROM template_tags g WHERE g.template_id = t.template_id AND g.tag = :tag)`（service 侧归一化 lower）
- `scope`：等值过滤（public / user / chat）
- 恒定排除 `archived_at IS NOT NULL`
- `query` 为空：不筛全文，仅按融合分排序（收藏热度序）

### 5.4 接口

- **API**：`GET /templates/search-v2?q=&tag=&scope=&limit=20&offset=0` → `{results: [{template_id, name, score, tags, favorite_count}]}`
- **IM**：`/template-find <query> [#tag]`（`#` 前缀词作标签过滤）
- **旧接口**：`/templates/search`（Phase 7 trigram）与 `/templates/by-tag`（Phase 8）**保留不动**

### 5.5 测试策略（关键）

PG 分支本机无法跑真库，采用：
- SQLite 路径全量真测（融合排序 / 标签过滤 / scope / 空查询 / 分页 / 归档排除）
- 方言检测函数 `_is_postgres(engine)` 单测（mock dialect.name）
- PG 分支 SQL 构造以"分支函数存在 + 被 dialect 分发覆盖"冒烟（不执行真 SQL）
- 真实 PG 验证列入 Phase 10 联调清单

---

## 6. diff 升级（内容 hash + moved）

### 6.1 目标

修复 ADR-0021 已知局限：块重排序时旧算法误报 `changed`。升级后内容相同仅位置变 → 报 `moved`。

### 6.2 算法 v2（在 v1 同 type 分组内两阶段配对）

```
对每个 type 分组：
  阶段1（精确配对）：计算每项内容 hash = md5(json.dumps(item, sort_keys=True))
    a/b 侧按 hash 匹配（同 hash = 内容完全相同）：
      - index_a != index_b → moved
      - index 相同 → unchanged（不进输出）
  阶段2（余量按序配对）：阶段1 未配对的同 type 项，按出现顺序一一配对
      → 键集差异 → changed(fields)（修改过的块，内容已变无法 hash 命中）
  剩余：b 侧多余 → added；a 侧多余 → removed
```

### 6.3 输出结构（向后兼容）

```python
{"added": [...], "removed": [...], "changed": [...],
 "moved": [{"index_a": 0, "index_b": 2, "type": "heading"}]}   # 新增键
```

旧键语义不变；`render` 增加 `↔ [0→2] heading` 行；无差异判断同步覆盖 moved。

### 6.4 边界

- 内容修改 + 位置同时变：阶段2 按序配对报 changed（不报 moved，hash 不匹配）——符合直觉
- 重复块（同 type 同内容多份）：hash 多对多按序消耗，天然正确
- steps（按 tool 配对）同样走 v2 算法

---

## 7. 数据模型汇总（ORM 变更）

```python
# === Phase 9 ===  models.py 追加（总表数 16 → 17）

class CommentNotifyRow(Base):
    """Phase 9: 评论动作推送日志（同一 comment 只通知一次，ADR-0025）。"""
    __tablename__ = "comment_notify_log"

    comment_id: Mapped[str] = mapped_column(String, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    notified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
```

无其他表结构变更；tsvector 用表达式查询（PG 生产可后补 GIN 索引，见 §10）。

---

## 8. API 与 IM 指令

### 8.1 FastAPI 变更（2 新路由 + 1 钩子）

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| GET | `/templates/search-v2` | `q / tag / scope / limit / offset` | `{results: [{template_id, name, score, tags, favorite_count}]}` |
| GET | `/templates/{template_id}/diff` | `?v_a=&v_b=` | 不变（内部升级，新增 `moved` 键） |
| startup | — | `create_app(..., auto_sync_worker=...)` | 注入则启动后台轮询 |

### 8.2 IM 指令（process_phase9，1 条）

| 指令 | 用法 | 动作 |
|---|---|---|
| `/template-find` | `/template-find blast #bio` | 融合检索，`#` 词作标签；IM 渲染 `name (score, ❤N, #tags)` |

未匹配 → 回落 `process_phase8`（链式）。`/comments-sync` `/comment-apply` 等 Phase 8 指令语义不变。

---

## 9. 测试策略

### 9.1 测试矩阵（计划 42，允许 +5 浮动）

| 层 | 文件 | 计划数 | 覆盖 |
|---|---|---|---|
| 单元 | `test_comment_notify_repo.py` | 3 | insert+has / 批量 / 跨 doc 索引 |
| 单元 | `test_comment_notify_service.py` | 5 | 推送新 pending / 去重不重发 / 跳过普通评论 / 跳过已处理 / 消息含 apply 提示 |
| 单元 | `test_auto_sync_worker.py` | 4 | tick 同步绑定 doc / 跳过过期绑定 / 跳过未绑定 / 同 doc 多 session 去重 |
| 单元 | `test_unified_search_service.py` | 7 | name 命中排序 / tag 过滤 / fav boost / scope / 空查询热度序 / 归档排除 / 方言检测 |
| 单元 | `test_diff_service_v2.py` | 5 | moved 检测 / 同位置不变 / 修改按序 changed / added+removed / render ↔ |
| 单元 | `test_orchestrator_phase9.py` | 1 | /template-find smoke |
| 集成 | `test_templates_phase9_api.py` | 3 | search-v2 路由 / tag 参数 / 503 未配置 |
| E2E | `test_e2e_phase9_e1_e6.py` | 6 | E1-E6（下） |

### 9.2 E2E 场景（E1-E6）

| # | 场景 | 断言核心 |
|---|---|---|
| E1 | 自动闭环：stub 评论 → worker.tick → sync 落库 + IM 推送 owner | notify=1、消息含 `/comment-apply` |
| E2 | 推送去重：同评论再 tick | notify=0、IM 不再发 |
| E3 | 推送后 apply 闭环 | applied=1、后续 tick 无 pending |
| E4 | 融合检索：fav 高的排前 | score 排序正确、含 tags/fav_count |
| E5 | 标签 + 全文组合过滤 | 仅命中带标签且匹配查询的模板 |
| E6 | diff moved：块换位 | moved 长度 1、changed 空、render 含 `↔` |

### 9.3 回归门

- Phase 8 累计 445 测试 **0 回归**（重点：diff 旧 7 测试、搜索旧 4 测试）
- 新增 ≥ 42 → 总数 ≥ 487

---

## 10. 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| 轮询打爆飞书 API 限流 | 中 | 复用 RateLimiter；interval 默认 300s；异常隔离单 doc |
| 多实例部署重复轮询 | 低 | MVP 单实例；Phase 10 加分布式锁（列明） |
| 推送轰炸 owner | 中 | notify 日志表持久去重；仅动作评论；空集不发消息 |
| SQLite 无 tsvector 导致测试假绿 | 中 | 方言显式分支 + PG 路径冒烟；真 PG 验证列 Phase 10 联调清单 |
| PG 大表全表 tsvector 计算慢 | 低 | 生产建议 GIN 索引（`CREATE INDEX ... USING GIN(to_tsvector(...))`），迁移脚本 Phase 10 补 |
| diff v2 改动破坏 Phase 8 语义 | 中 | 输出仅增 `moved` 键；旧 7 个 diff 测试全量回归把关 |
| asyncio worker 在测试环境泄漏 | 低 | `create_app` 不传 worker 不启动；worker.stop() 幂等 |

---

## 11. ADR 清单（Phase 9）

| ADR | 标题 | 决策 |
|---|---|---|
| 0024 | phase9-comment-auto-sync-polling | 后台轮询（非 webhook、非手动） |
| 0025 | phase9-notify-after-sync-dedup | sync 后即时推送 + notify 日志去重 |
| 0026 | phase9-unified-search-dialect | 融合检索单入口 + 方言分支降级 |
| 0027 | phase9-diff-hash-moved | 内容 hash 配对 + moved 检测 |

> 注：Q1（范围组合）为流程决策不单独立 ADR，故本阶段 4 个（较 Phase 8 少 1）。

---

## 12. 后续动作与阶段门

**Phase 9 实施前**：

1. ✅ 本 spec 用户确认（A,A,A,A,A 已批）
2. ⏭️ 写 4 个 ADR（0024-0027）
3. ⏭️ 写 Phase 9 plan（writing-plans skill）
4. ⏭️ 用户评审 plan + ADR
5. ⏭️ Inline 实施（沿用 Phase 3-8 节奏）

**Phase 10 候选范围**：

| 模块 | 候选 |
|---|---|
| 真实联调 | 飞书测试租户 webhook / 真 LLM / 真 PG（tsvector + GIN）/ venv 依赖锁定 / CI |
| 评论 | webhook 实时推送 / 分页 / tombstone |
| 检索 | PG 全文中文分词（zhparser）/ 标签治理 |
| 模板 | merge / branch / conflict |
| 领域 | AlphaFold / MSA / 本地 DB 同步 |
| 运维 | 分布式轮询锁 / OTel trace |

**Phase 9 阶段门**：

| 阶段门 | 标准 |
|---|---|
| 设计门 | 本 spec 用户确认（✅ 2026-08-26 A,A,A,A,A） |
| ADR 门 | 4 个 ADR 用户可评审 |
| 实施门 | plan 用户确认后 inline 实施 |
| 测试门 | ≥ 42 新测试通过；445 老测试 0 回归 |

---

## 13. 与 Phase 8 的接口契约（不变项）

以下 Phase 8 接口**签名与语义完全不变**，Phase 9 只做加法：

- `CommentSyncService.sync / list_stored`（worker 只是其新调用方）
- `CommentActionService.apply / parse_action`（notify 推送的 apply 提示指向既有指令）
- `CommentRepo` 全部方法（notify 只读 pending，写走新 notify repo）
- `TagService / FavoriteService / find_by_tag / favorite`（search-v2 只读两表）
- `/templates/search`、`/templates/by-tag`、`/templates/favorites/*` 路由
- `diff()` 返回结构的既有键（仅新增 `moved`）
- 既有 31 条路由 + process_phase8 指令链

---

## 14. 实施结果（交付后填写）

待 plan 实施 + 回归后填写（commit / 实际测试数 / 文件清单 / 累计测试曲线）。
