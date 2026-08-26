# Phase 9 实施计划 — 协作智能化

> 日期：2026-08-26
> Spec: [`../specs/2026-08-26-feishu-research-agent-phase9-design.md`](../specs/2026-08-26-feishu-research-agent-phase9-design.md)
> ADRs: 0024-0027
> 模式：Inline 实施（沿用 Phase 3-8 节奏，每任务含实现 + 测试 + 冒烟）
> 基线：Phase 8 提交 49c3b2b，445 测试全绿

---

## 任务总览

| # | 任务 | 文件 | 新测试 | 依赖 |
|---|---|---|---|---|
| T1 | ORM：CommentNotifyRow + CommentNotifyRepo | models.py + comment_notify_repo.py | 3 | — |
| T2 | diff v2：hash 配对 + moved | diff_service.py 修改 | 5 | — |
| T3 | TemplateRepo.search_v2（方言分支 + 融合排序） | template_repo.py 修改 | 7 | — |
| T4 | CommentNotifyService | notify_service.py | 5 | T1 |
| T5 | CommentAutoSyncWorker | auto_sync_worker.py | 4 | T4 |
| T6 | UnifiedSearchService | unified_search_service.py | 并入 T3 测 | T3 |
| T7 | Settings + gateway（2 路由 + startup 钩子） | settings.py / app.py 修改 | 3 | T3/T5 |
| T8 | Orchestrator process_phase9 + /template-find | app.py 修改 | 1 | T7 |
| T9 | E2E E1-E6 | test_e2e_phase9_e1_e6.py | 6 | T1-T8 |
| T10 | 全量回归 + spec §14 + 测试总结 + commit | 文档 | — | 全部 |

**计划新增测试：39**（spec 矩阵 42 中 7 项检索测试合并计入 T3/T6 一处，实际文件计数如下）→ 预期总数 **484±5**。

---

## T1: CommentNotifyRow + Repo

**修改** `persistence/models.py` Phase 9 段（spec §7 逐字段）。

**新建** `persistence/repositories/comment_notify_repo.py`：

```python
class CommentNotifyRepo:
    def has(self, comment_id: str) -> bool
    def insert_many(self, *, doc_id: str, comment_ids: list[str]) -> int  # 跳过已存在，返回新增数
    def list_by_doc(self, doc_id: str) -> list[str]
```

**测试** `tests/unit/test_comment_notify_repo.py`（3）：

1. `test_insert_and_has`：insert_many 后 has=True，重复 insert_many 返回 0
2. `test_insert_many_partial_new`：2 新 + 1 已存在 → 返回 2，总数 3
3. `test_list_by_doc`：按 doc 过滤命中

夹具：SQLite in-memory + StaticPool（照 test_comment_repo.py）。

---

## T2: diff v2（hash 配对 + moved）

**修改** `orchestrator/templates/diff_service.py`：

1. 新增 `_content_hash(item) -> str`：`md5(json.dumps(item, sort_keys=True, ensure_ascii=False))`
2. `_diff_lists(a, b, key)` 重写为两阶段（spec §6.2）：
   - 阶段1：组内按 hash 配对（dict: hash → [(idx, item)...] 队列，a/b 各自消耗）→ index 不同记 `moved`，相同记 unchanged
   - 阶段2：组内余量按序配对 → 键集差异 → `changed(fields)`
   - 剩余 b → `added`（带 index_b）；剩余 a → `removed`（带 index_a）
   - 返回 dict 新增 `"moved"` 键（列表，元素 `{index_a, index_b, type}`）
3. `render`：moved 渲染 `↔ [a→b] type`；"无差异"判断加 moved 为空条件
4. **不变**：`added/removed/changed` 元素结构、`diff()` 顶层键、meta 语义

**测试** `tests/unit/test_diff_service_v2.py`（5，真函数直测 + mock version_repo）：

1. `test_moved_detected`：`[heading A, code X]` → `[code X, heading A]` → moved=2（或按实现断言 ≥1 且 changed 空）
2. `test_identical_same_position_no_output`：完全相同 → 四列表全空
3. `test_modified_falls_to_changed`：`[heading A]` → `[heading B]` → changed fields=["text"]、moved 空
4. `test_mixed_added_removed`：增删并存计数正确
5. `test_render_moved_symbol`：含 `↔ [0→1]`、`（无差异）` 在全同时不出现

**回归把关**：跑旧 `tests/unit/test_diff_service.py`（7 个）必须全绿（输出兼容验证）。

---

## T3: TemplateRepo.search_v2 + 方言分支

**修改** `persistence/repositories/template_repo.py`：

```python
def _is_postgres(session) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"

class TemplateRepo:
    def search_v2(self, *, query="", tag=None, scope=None,
                  limit=20, offset=0) -> list[TemplateRow]:
        """Phase 9 融合检索（ADR-0026）：全文(方言分支) + 标签 + 收藏 boost。"""
        # 基础：archived IS NULL [+ scope]
        # 全文分支：
        #   PG: to_tsvector('simple', coalesce(name,'')||' '||coalesce(description,''))
        #       @@ plainto_tsquery(:q)，text_score = ts_rank(...)
        #   SQLite/其他: ilike 分支，name 命中 2.0 / 仅 desc 1.0
        # tag 过滤：EXISTS(template_tags g WHERE g.template_id=id AND g.tag=:tag)
        # 融合：text_score + LEAST(fav_count,5)*0.5，fav_count 为相关子查询
        # ORDER BY final DESC, updated_at DESC LIMIT/OFFSET
```

实现细节：SQLAlchemy Core 表达式（`sqlalchemy.func.to_tsvector / plainto_tsquery / ts_rank`，`sqlalchemy.select` 子查询计数）。SQLite 分支用 `case()` 表达 name/desc 命中分。

**测试** `tests/unit/test_unified_search_service.py`（7，SQLite 真库）：

1. `test_name_match_ranks_higher`：name 命中模板排在仅 desc 命中前
2. `test_tag_filter`：只返回带该标签模板
3. `test_favorite_boost`：弱匹配 + 3 收藏 排在 强匹配 + 0 收藏 前（2.0 vs 1.0+1.5）
4. `test_scope_filter`
5. `test_empty_query_hotness_order`：无 q 时按收藏数排序
6. `test_archived_excluded`
7. `test_is_postgres_detection`：mock engine dialect.name 为 postgresql/sqlite 两分支（直接测 `_is_postgres`）

---

## T4: CommentNotifyService

**新建** `orchestrator/templates/notify_service.py`：

```python
class CommentNotifyService:
    def __init__(self, comment_repo, notify_repo, action_parser=parse_action) -> None: ...

    def notify_new_pending(self, *, doc_id, owner_open_id, chat_id) -> dict:
        # 1. pending = comment_repo.list_pending(doc_id)
        # 2. hits = [c for c in pending
        #            if self.action_parser(c.text) is not None
        #            and not notify_repo.has(c.comment_id)]
        # 3. 空 → {"notified": 0}（不发 IM）
        # 4. IM 推送（im_adapter.reply(chat_id, msg)）：
        #    "[评论动作] doc {doc_id} 有 {n} 条待处理：
        #     - {user_name}: {text}（每条一行，截断 50 字）
        #     发送 /comment-apply {doc_id} 应用"
        # 5. notify_repo.insert_many(doc_id=..., comment_ids=...)
        # 6. 返回 {"notified": n}
```

构造函数注入 `im_adapter`（`reply(chat_id, text)` 接口，与 IMAdapter 一致，测试用 MagicMock）。

**测试** `tests/unit/test_comment_notify_service.py`（5，SQLite 真 repo + mock IM）：

1. `test_notify_new_pending_sends_im`：1 条动作评论 → notified=1，IM 消息含 `/comment-apply`
2. `test_dedup_no_resend`：二次调用 → notified=0，IM 仅发 1 次
3. `test_skips_plain_comments`：普通评论不触发
4. `test_skips_processed`：processed_at 非空的不触发
5. `test_empty_no_message`：无命中不调 IM

---

## T5: CommentAutoSyncWorker

**新建** `orchestrator/templates/auto_sync_worker.py`：

```python
class CommentAutoSyncWorker:
    def __init__(self, *, session_repo, sync_service, notify_service,
                 interval_sec: int = 300) -> None: ...

    def tick(self) -> dict:
        """单轮：扫描绑定 doc → sync → notify。返回 {"synced": n, "notified_total": m}。"""
        # list_active() → 过滤 bound_doc_id 非空且未过期
        # 按 doc_id 去重（保留第一个 session 的 owner/chat）
        # 每 doc：sync_service.sync(doc_id)；异常 → logger.exception 继续
        #         notify_service.notify_new_pending(doc_id, owner, chat)

    async def _run_loop(self) -> None:  # while True: sleep(interval); tick()
    def start_async(self) -> None:      # asyncio.create_task（无运行循环时安全跳过）
    def stop(self) -> None:             # 取消 task，幂等
```

时间源：`_now()` 方法（默认 `datetime.now(timezone.utc)`，测试可注入固定时钟）；过期判断 `bind_expires_at > now`。

**测试** `tests/unit/test_auto_sync_worker.py`（4，SQLite 真 session_repo + stub sync/notify service）：

1. `test_tick_syncs_bound_doc`：1 active 绑定 session → sync 被调 1 次 + notify 被调 1 次
2. `test_skips_expired_bind`：bind_expires_at 过去 → 均不调
3. `test_skips_unbound`：bound_doc_id None → 均不调
4. `test_same_doc_dedup`：2 session 绑同一 doc → sync 仅 1 次

---

## T6: UnifiedSearchService

**新建** `orchestrator/templates/unified_search_service.py`：

```python
class UnifiedSearchService:
    def __init__(self, template_repo, tag_repo=None) -> None: ...

    def search(self, *, query="", tag=None, scope=None, limit=20, offset=0) -> list[dict]:
        # repo.search_v2(...) → rows
        # 输出 dict：{template_id, name, score, tags, favorite_count}
        #   tags: tag_repo.list_by_template(tid)（tag_repo 可选 None → []）
        #   favorite_count: 本轮不回读（简化：score 已含 boost；输出字段留 0 占位 + 注释）
        # tag 归一化 strip().lower()

    def render(self, results: list[dict]) -> str:
        # "- {name} (score={score:.1f}, #{tags})" IM 文本
```

> favorite_count 回读需 favorite_repo 聚合查询；为控制范围，`search_v2` 返回行 + 服务层用 tag_repo 补 tags；favorite_count 以 `score` 内含 boost 呈现（字段保留，值由 repo 侧子查询返回——若实现代价高则置 0 并在 spec §14 记录）。

**测试**：并入 T3 文件（test_unified_search_service.py 追加 2 个）：

8. `test_service_formats_results`：输出含 template_id/name/score/tags 键
9. `test_service_tag_normalized`：`" BIO "` 传参命中 `bio` 标签模板

（T3 文件合计 9 测试）

---

## T7: Settings + Gateway

**修改** `config/settings.py`：`Settings` 追加 `comment_sync_interval_sec: int = 300`（dataclass 字段，默认值不破坏 load_settings）。

**修改** `gateway/app.py`：

1. `AppContext` + `create_app` 追加：`unified_search_service` / `auto_sync_worker`（2 个注入位）
2. 路由（照 Phase 8 风格）：

| 路由 | 调用 |
|---|---|
| GET `/templates/search-v2?q=&tag=&scope=&limit=&offset=` | `unified_search_service.search` → `{results: [...]}`；未配置返回 `{"results": []}` |
| GET `/templates/{template_id}/diff` | 已存在（内部 v2 升级），补 1 集成测试断言 moved 键 |

3. startup 钩子：

```python
if auto_sync_worker is not None:
    @app.on_event("startup")
    def _start_worker():
        auto_sync_worker.start_async()
```

**测试** `tests/integration/test_templates_phase9_api.py`（4）：

1. `test_search_v2_route`：mock service → 200 + results
2. `test_search_v2_not_configured_empty`
3. `test_diff_route_has_moved_key`：mock diff service 返回含 moved 的 dict → 响应透传
4. `test_search_v2_tag_param_forwarded`：断言 service 收到归一化前原值（归一化在 service）

---

## T8: Orchestrator process_phase9

**修改** `orchestrator/app.py` 追加 `process_phase9()`（照 phase8 结构）：

```
/template-find <query> [#tag]   → unified_search_service.search + render + IM
未匹配 → self.process_phase8(incoming)
```

解析：`text.split()`，`#` 开头 token 为 tag（可多个取第一个），其余 join 为 query；query 与 tag 均空 → 用法提示。

**测试** `tests/unit/test_orchestrator_phase9.py`（1）：`/template-find blast #bio` smoke（service mock + IM mock，断言参数与回复）。

---

## T9: E2E E1-E6

**新建** `tests/integration/test_e2e_phase9_e1_e6.py`，真 SQLite + 真 repo/service + stub client + mock IM（spec §9.2）：

- 通用夹具：engine/session + 全 repo + CommentSyncService(stub client) + CommentNotifyService(mock IM) + AutoSyncWorker + UnifiedSearchService + TemplateRepo 数据种子
- E1 自动闭环：注入评论 → `worker.tick()` → sync 落库 + IM 推送含 `/comment-apply`
- E2 推送去重：再 tick → IM 仍 1 次、notified=0
- E3 推送后 apply：`action.apply` → applied=1 → 再 tick 无新 pending
- E4 融合排序：模板 A(name 命中 0 fav) vs B(desc 命中 3 fav) → B score 更高排前
- E5 标签组合：只命中"带 bio 标签且文本匹配"的模板
- E6 diff moved：v1 `[heading A, code X]` → v2 `[code X, heading A]` → moved ≥1、changed 空、render 含 `↔`

**6 测试。**

---

## T10: 回归 + 收尾

1. `python -m pytest tests/ -q` 全量 → 484±5 全绿，0 回归（重点盯旧 diff 7 测试 + 旧搜索 4 测试 + Phase 8 全部 56）
2. spec §14 填写：commit / 实际测试数 / 文件清单 / 累计曲线（445 → 新值）
3. 测试总结文件追加 Phase 9 记录（含问题与建议）
4. commit：`feat(phase9): auto comment loop + unified search + diff v2 (moved)`

---

## 执行顺序与检查点

```
T1 → T2 → T3/T6 → T4 → T5 → T7 → T8 → T9 → T10
        T2 完成立即跑旧 diff 测试（兼容验证）
        T3 完成跑旧搜索测试
        T9 后跑 Phase 8 E2E（防链式回归）
```

风险预案（对应 spec §10）：

- SQLite `case()` 融合分表达式复杂 → 退化为 Python 侧排序（取 limit*3 行后本地算 final_score，语义不变）
- `on_event("startup")` 在 TestClient 下自动触发 worker → 测试不注入 worker（默认 None，spec 已定）
- MagicMock session_repo 返回的 SessionRow 属性缺失 → 真 SQLite 夹具（T5 已按真库设计）
