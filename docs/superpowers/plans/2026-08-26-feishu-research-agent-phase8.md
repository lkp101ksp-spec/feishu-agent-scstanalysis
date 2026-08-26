# Phase 8 实施计划 — 评论驱动闭环

> 日期：2026-08-26
> Spec: [`../specs/2026-08-26-feishu-research-agent-phase8-design.md`](../specs/2026-08-26-feishu-research-agent-phase8-design.md)
> ADRs: 0019-0023
> 模式：Inline 实施（沿用 Phase 3-7 节奏，每任务含实现 + 测试 + 冒烟）
> 基线：Phase 7 提交 d2663ec，389 测试全绿

---

## 任务总览

| # | 任务 | 新文件 | 新测试 | 依赖 |
|---|---|---|---|---|
| T1 | ORM：CommentRow / TemplateTagRow / TemplateFavoriteRow | models.py 修改 | — | — |
| T2 | CommentRepo（幂等 upsert / pending / mark_processed） | persistence/repositories/comment_repo.py | 4 | T1 |
| T3 | TagRepo + FavoriteRepo | 2 个 repo 文件 | 6 | T1 |
| T4 | CommentSyncService（展平 + upsert + 统计） | orchestrator/templates/comment_sync_service.py | 4 | T2 |
| T5 | CommentActionService（解析 + apply + 打标） | orchestrator/templates/comment_action_service.py | 7 | T4 |
| T6 | VersionDiffService（块对齐 + IM 渲染） | orchestrator/templates/diff_service.py | 5 | — |
| T7 | TagService + FavoriteService | 2 个 service 文件 | 6 | T3 |
| T8 | Gateway 10 路由（7 组资源） | gateway/app.py 修改 | 10 | T4-T7 |
| T9 | Orchestrator process_phase8 + 6 IM 指令 | orchestrator/app.py 修改 | 1 | T8 |
| T10 | E2E E1-E8 | tests/integration/test_e2e_phase8_e1_e8.py | 8 | T2-T9 |
| T11 | 全量回归 + spec §14 填写 + commit | spec 修改 | — | 全部 |

**计划新增测试：51**（spec 下限 48）→ 预期总数 **440**。

---

## T1: ORM 三新表

**修改** `persistence/models.py`，Phase 8 段追加（按 spec §7 逐字段）：

1. `CommentRow`（comments）：comment_id PK / doc_id idx / block_id / user_id / user_name / text / is_reply / parent_comment_id / resolved / created_at / synced_at（onupdate）/ processed_at
2. `TemplateTagRow`（template_tags）：tag_id PK / template_id idx / tag idx / created_by / created_at + `UniqueConstraint("template_id", "tag")`
3. `TemplateFavoriteRow`（template_favorites）：favorite_id PK / template_id idx / user_open_id idx / created_at + `UniqueConstraint("template_id", "user_open_id")`
4. 头注 `__table_args__` 用 `from sqlalchemy import UniqueConstraint`

**验证**：`pytest tests/unit/test_template_audit.py`（create_all 建全表，若模型语法错此处即红）

---

## T2: CommentRepo

**新建** `persistence/repositories/comment_repo.py`：

```python
class CommentRepo:
    def upsert_one(self, *, comment_id, doc_id, block_id, user_id,
                   user_name, text, is_reply, parent_comment_id,
                   resolved) -> tuple[CommentRow, bool]:  # (row, is_new)
        # 存在：更新 text/resolved/synced_at（不动 processed_at）
        # 不存在：insert
    def list_by_doc(self, doc_id, block_id=None) -> list[CommentRow]
    def list_pending(self, doc_id) -> list[CommentRow]  # processed_at IS NULL
    def mark_processed(self, comment_id) -> None
```

**测试** `tests/unit/test_comment_repo.py`（4）：

1. `test_upsert_insert_then_idempotent_update`：两次 upsert 同 id → 1 行，text 更新
2. `test_upsert_preserves_processed_at`：mark 后再 upsert → processed_at 不为 None
3. `test_list_pending_filters_processed`：2 条 pending + 1 条 processed → 返回 2
4. `test_list_by_doc_block_filter`：block_id 过滤命中

夹具：SQLite in-memory + StaticPool（照抄 test_template_audit.py）。

---

## T3: TagRepo + FavoriteRepo

**新建** `persistence/repositories/template_tag_repo.py`：

```python
class TemplateTagRepo:
    def add(self, *, tag_id, template_id, tag, created_by) -> TemplateTagRow
        # unique(template_id, tag) 冲突 → 静默返回既有行（查询先判）
    def remove(self, *, template_id, tag) -> None  # 不存在静默
    def list_by_template(self, template_id) -> list[str]  # 标签列表
    def find_template_ids_by_tag(self, tag) -> list[str]
```

**新建** `persistence/repositories/template_favorite_repo.py`：

```python
class TemplateFavoriteRepo:
    def add(self, *, favorite_id, template_id, user_open_id) -> None  # 幂等
    def remove(self, *, template_id, user_open_id) -> None  # 幂等
    def list_by_user(self, user_open_id) -> list[str]  # 倒序
```

**测试**（6）：

- `test_template_tag_repo.py`（3）：add 幂等（重复 add 仍 1 行）/ remove / find_by_tag 命中
- `test_template_favorite_repo.py`（3）：add 幂等 / remove / list_by_user 倒序

---

## T4: CommentSyncService

**新建** `orchestrator/templates/comment_sync_service.py`：

```python
class CommentSyncService:
    def __init__(self, client, comment_repo) -> None: ...

    def sync(self, *, doc_id: str) -> dict:
        # 1. client.list_comments(doc_id) → items
        # 2. 展平：root → upsert_one(is_reply=False)
        #    每条 reply → upsert_one(is_reply=True, parent=root_id,
        #                             comment_id=reply 自身 id)
        # 3. 返回 {"fetched": 拉取数(root+reply), "new": n, "updated": u}

    def list_stored(self, *, doc_id, block_id=None) -> list
```

reply 的 comment_id 取 `reply.get("reply_id") or reply.get("comment_id")`，user 取 `reply.get("user_name")`。

**测试** `tests/unit/test_comment_sync_service.py`（4）：

1. root+2 replies → 3 行落库，parent 关联正确
2. 重复 sync → new=0, updated=3，仍 3 行
3. 空评论列表 → fetched=0, new=0
4. 统计返回值 fetched/new/updated 数值正确（混合新旧）

repo 用真 SQLite（集成式单测，验证幂等语义最可靠）。

---

## T5: CommentActionService

**新建** `orchestrator/templates/comment_action_service.py`：

```python
@dataclass
class ParsedAction:
    kind: str        # replan / revise / add-step
    template_id: str
    payload: str     # note / desc / tool_name

def parse_action(text: str) -> ParsedAction | None:
    # "/replan t1 note..." → 前缀后切 2 段（maxsplit=2）
    # "/revise t1 desc..." 同理
    # "/add-step t1 tool" 同理（payload=tool，无空格则失败）
    # 其余 → None

class CommentActionService:
    def __init__(self, comment_repo, template_repo, version_service) -> None: ...

    def apply(self, *, doc_id: str, caller_open_id: str) -> dict:
        # 遍历 comment_repo.list_pending(doc_id)
        # parsed = parse_action(c.text)；None → skipped += 1
        # 模板不存在 → failed(reason=not_found)
        # owner != caller → failed(reason=not_owner)
        # kind 分发：
        #   replan: desc = f"{old}\n[replan by {caller}] {payload}"
        #   revise: desc = payload
        #   add-step: type != 'subplan' → failed(not_subplan)
        #             steps_json = json.loads(or "[]") + [{"tool": payload, "args": {}}]
        # 成功 → template_repo.upsert + version_service.on_template_upsert
        #       + comment_repo.mark_processed
        # 返回 {"applied", "skipped", "failed", "details": [{comment_id, kind, status, reason?}]}
```

**测试** `tests/unit/test_comment_action_service.py`（7）：

1. parse_action 三前缀各自解析正确
2. parse_action 普通文本 → None
3. apply replan：description 追加 + version_service 调用 + mark_processed
4. apply revise：description 覆盖
5. apply add-step：steps_json 追加一步（真 repo 验证 JSON）
6. 非 owner → failed=1, reason=not_owner, upsert 未被调
7. add-step 到 block 模板 → failed=1, reason=not_subplan

（parse 3 前缀合并为 1 测试亦可，保持 7 个总数）

---

## T6: VersionDiffService

**新建** `orchestrator/templates/diff_service.py`：

```python
class VersionDiffService:
    def __init__(self, version_repo, template_repo) -> None: ...

    def diff(self, *, template_id, v_a: int, v_b: int) -> dict:
        # va/vb 不存在 → ValueError
        # _diff_lists(list_a, list_b, key="type") → {added, removed, changed}
        # blocks 用 key="type"，steps 用 key="tool"
        # meta: name_changed / description_changed
    def render(self, diff: dict) -> str:
        # "+ [i] type / - [i] type / ~ [i] type (字段: a, b)"
        # "~ name: old → new" / steps 汇总行

def _diff_lists(a: list[dict], b: list[dict], key: str) -> dict:
    # 同 key 值按出现顺序配对；配对成功比键集差异 → changed(fields=差异键名)
    # b 中未配对 → added（带 b 侧 index）；a 中未配对 → removed（带 a 侧 index）
```

**测试** `tests/unit/test_diff_service.py`（5）：

1. added：v2 新增 code 块 → blocks.added 长度 1
2. removed：v2 删 quote 块 → blocks.removed 长度 1
3. changed：heading.text 变 → changed[0].fields == ["text"]
4. 同版本 → 三列表全空
5. render：changed + name 变 → 文本含 "~" 和 "name:"

repo 用 MagicMock（get_by_version 返回带 blocks_json/steps_json 的 mock）。

---

## T7: TagService + FavoriteService

**新建** `orchestrator/templates/tag_service.py`：

```python
class TagService:
    def __init__(self, tag_repo, template_repo) -> None: ...
    def attach(self, *, template_id, tag, caller_open_id) -> None
        # 模板存在 & 未归档 & owner==caller（PermissionError）
        # tag 归一化 strip().lower()
    def detach(self, *, template_id, tag, caller_open_id) -> None  # 同权限
    def list_tags(self, template_id) -> list[str]
    def find_by_tag(self, tag) -> list  # tag_repo.find_template_ids_by_tag
        # → template_repo.get 逐个取（过滤 archived/None）
```

**新建** `orchestrator/templates/favorite_service.py`：

```python
class FavoriteService:
    def __init__(self, favorite_repo, template_repo) -> None: ...
    def favorite(self, *, template_id, caller_open_id) -> None  # 存在校验
    def unfavorite(self, *, template_id, caller_open_id) -> None
    def list_favorites(self, user_open_id) -> list  # id → 模板行
```

**测试**（6）：

- `test_tag_service.py`（3）：attach 非 owner PermissionError / attach+list_tags 归一化（" BLAST " → "blast"）/ find_by_tag 过滤归档
- `test_favorite_service.py`（3）：favorite+list 命中 / unfavorite 后列表空 / 模板不存在 ValueError

---

## T8: Gateway 10 路由

**修改** `gateway/app.py`：

1. `AppContext` + `create_app` 参数追加 5 个：`comment_sync_service` / `comment_action_service` / `diff_service` / `tag_service` / `favorite_service`
2. 路由（错误处理照 Phase 7 风格：503 未配置 / 403 PermissionError / 404 ValueError）：

| 路由 | 调用 |
|---|---|
| POST `/comments/{doc_id}/sync` | `comment_sync_service.sync` |
| GET `/comments/{doc_id}/stored` | `comment_sync_service.list_stored` |
| POST `/comments/{doc_id}/apply-actions` | `comment_action_service.apply` |
| GET `/templates/{template_id}/diff` | `diff_service.diff(v_a, v_b)` |
| POST `/templates/{template_id}/tags` | `tag_service.attach` |
| DELETE `/templates/{template_id}/tags` | `tag_service.detach` |
| GET `/templates/by-tag/{tag}` | `tag_service.find_by_tag` |
| POST `/templates/{template_id}/favorite` | `favorite_service.favorite` |
| DELETE `/templates/{template_id}/favorite` | `favorite_service.unfavorite` |
| GET `/templates/favorites/{user_open_id}` | `favorite_service.list_favorites` |

**注意**：`/templates/by-tag` 与 `/templates/favorites` 是具体字面路径，必须注册在 `/templates/{template_id}/...` 之前或 FastAPI 路由顺序上先匹配字面量（实测 FastAPI 按注册顺序匹配，字面量路由先注册即可；既有 `/templates/search` `/templates/public` 同模式已验证）。

**测试**（10，MagicMock service + TestClient，照 test_templates_phase7_api.py 风格）：

`tests/integration/test_comments_phase8_api.py`（3）：sync / stored / apply
`tests/integration/test_templates_phase8_api.py`（7）：diff / tag-add / tag-del / by-tag / fav-add / fav-del / favorites

---

## T9: Orchestrator process_phase8

**修改** `orchestrator/app.py`：追加 `process_phase8()`（照 process_phase7 结构）：

```
/comments-sync <doc_id>       → sync_service.sync + IM 统计回复
/comment-apply <doc_id>       → action_service.apply + IM applied/skipped/failed
/template-diff <tid> <a> <b>  → diff_service.diff + render
/template-tag <tid> <tag>     → tag_service.attach
/template-favorite <tid>      → favorite_service.favorite
/template-favorites           → favorite_service.list_favorites
未匹配 → self.process_phase7(incoming)
```

异常分支（PermissionError / ValueError / service 未配置）照 phase6/7 模式 IM 回复 + 返回 status dict。

**测试** `tests/unit/test_orchestrator_phase8.py`（1）：`/template-favorites` smoke（service mock + IM mock，断言回复与 status）。

---

## T10: E2E E1-E8

**新建** `tests/integration/test_e2e_phase8_e1_e8.py`，全部用真 SQLite + 真 repo + 真 service（client 用 stub）：

- 通用夹具：engine + session + 全 repo + CommentClient stub（可注入 items）+ 各 service 真实例
- E1：导师评论 `/replan t1 增加对照组` → sync → owner apply → tpl.description 含 note、version_service 写入、comment.processed_at 非空
- E2：`/add-step t1 blast_local` → apply → steps_json 长度 +1
- E3：非 owner apply → failed=1 not_owner，模板未变
- E4：再次 apply → applied=0
- E5：revise 前后两版本 diff → changed 含 description 或 name，render 含 "~"
- E6：tag → find_by_tag 命中
- E7：favorite → list_favorites 命中；重复 favorite 仍 1 行
- E8：root+reply 展平 + block_id 过滤 → 2 行 + parent 关联

**8 测试**。

---

## T11: 回归 + 收尾

1. `python -m pytest tests/ -q` 全量 → 440±5 全绿，0 回归
2. spec §14 填写：commit / 实际测试数表 / 文件清单 / 累计曲线（389 → 新值）
3. 更新 plan 头部基线说明
4. commit：`feat(phase8): implement comment-driven loop (sync + actions + diff + tags/favorites)`

---

## 执行顺序与检查点

```
T1 → T2 → T3（并行可） → T4 → T5 → T6 / T7（并行） → T8 → T9 → T10 → T11
        每任务完成即跑该任务测试；T8 后跑全部集成；T11 全量回归
```

风险预案（对应 spec §10）：

- FastAPI 路由字面量冲突 → T8 已排顺序；若仍冲突改路径 `/templates/by-tag` → `/tag-templates/{tag}`
- SQLite UniqueConstraint 幂等语义 → repo 层先查后写（不依赖 IntegrityError）
- MagicMock spec 属性默认非 None → _tpl 助手显式置 archived_at=None（Phase 7 教训）
