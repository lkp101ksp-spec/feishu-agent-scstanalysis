# 飞书科研闭环 Agent — Phase 8 设计稿

> 日期：2026-08-26
> 状态：Phase 8 设计稿（已确认 A,A,A,A,A 方案）
> 范围：评论持久化 + 评论触发动作 + 版本块级 diff + 模板标签/收藏
> 推迟：评论 webhook 实时推送 / LLM 意图理解 / tsvector 全文检索 / 模板 merge-branch / 工具 ACL / AlphaFold（Phase 9）
> 前置：[Phase 7](./2026-08-09-feishu-research-agent-phase7-design.md)（评论 fetch-only + 搜索 + 公共模板 + fork，389 测试）
> 后置：Phase 8 实施计划 [`../plans/2026-08-26-feishu-research-agent-phase8.md`](../plans/2026-08-26-feishu-research-agent-phase8.md)

---

## 1. 范围与目标

### 1.1 Phase 8 主题

**评论驱动闭环**。Phase 7 评论是只读的（fetch + render，重启即失），Phase 8 让评论真正驱动 agent 动作：导师在飞书 doc 评论 → 用户 `/comments-sync` 落库 → `/comment-apply` 执行评论中的指令（replan / revise / add-step）→ 模板自动出新版本 → `/template-diff` 查看两个版本差异。配套标签与收藏，让 Phase 7 公共模板库可用性闭环。

### 1.2 范围（4 子系统）

| 模块 | Phase 8 内容 | Phase 8 不做（推迟）|
|---|---|---|
| **评论持久化** | `comments` 表；`/comments-sync` 按需增量同步；comment_id 幂等 upsert；root+reply 展平落库；resolved 状态跟踪 | webhook 实时推送 / 评论写回飞书 |
| **评论触发动作** | 评论正文指令前缀（`/replan` `/revise` `/add-step`）；owner 显式 apply；执行后 version bump + 标记 processed | LLM 意图理解 / 自动触发 / agent 回复评论 |
| **版本 diff** | 块级结构化 diff（added / removed / changed + 字段级摘要）；IM 文本渲染（`+ / - / ~`） | 渲染文本 unified diff / inline 逐字符 diff |
| **标签与收藏** | `template_tags` 多对多 + `template_favorites` user↔template；按标签检索；收藏列表 | tsvector / 标签推荐 / 收藏计数排序 |

### 1.3 关键约束（继承 Phase 1-7）

- **评论源仍是飞书**：本地 `comments` 表是缓存快照，sync 为幂等 upsert；不做双向写回
- **动作确定性**：指令前缀解析（字符串匹配），不引入 LLM 判断；AST 级可测试
- **权限**：apply 动作要求 caller 是目标模板 owner（评论作者可以是任何人，执行权在 owner）
- **版本硬上限 10**（Phase 6 约束）继续生效；每次动作执行都走 `VersionService.on_template_upsert`
- 4 个子系统**互不破坏** Phase 7 fetch-only 评论、搜索、公共模板审核、fork

### 1.4 关键决策（用户已批：A,A,A,A,A）

| # | 决策点 | 选择 | ADR |
|---|---|---|---|
| 1 | Phase 8 范围 | A：评论驱动闭环组合（4 子系统） | — |
| 2 | 评论持久化策略 | A：手动/按需增量同步（`/comments-sync`），comment_id 幂等 upsert | 0019 |
| 3 | 评论触发机制 | A：指令前缀约定（`/replan` `/revise` `/add-step`）+ owner 显式 apply | 0020 |
| 4 | 版本 diff 粒度 | A：块级结构化 diff（block 对齐 + 字段级摘要） | 0021 |
| 5 | 标签/收藏数据模型 | A：独立关联表（tag 多对多 + favorite 一对多） | 0022 |
| 6 | 动作执行权限 | 仅模板 owner 可 apply（评论作者任意） | 0023 |

### 1.5 不做清单（明确边界）

- 评论 webhook 实时推送（需公网回调，部署门槛高）
- 评论写回飞书（POST comment/reply）
- LLM 评论意图理解（不确定、难测试）
- 评论自动触发动作（必须 owner 显式 apply，防止误执行）
- tsvector 全文检索 / ES
- 标签推荐 / 热门标签 / 收藏计数排序
- 模板 merge / branch / conflict
- 工具 ACL / 工具在线热重载（Phase 6 已有本地热加载）
- AlphaFold / 多序列比对 / 本地 DB 自动同步
- Phase 2-7 推迟项

---

## 2. 总体架构

```
FastAPI（Phase 7 不变 + 7 新路由）
 └─ Orchestrator.process_phase8(incoming)
   ├─ process_phase7 链（不变）
   ├─ CommentSyncService.sync(doc_id)
   │  ├─ CommentClient.list_comments（Phase 7 复用）
   │  └─ CommentRepo.upsert_many（root+reply 展平，幂等）
   ├─ CommentActionService.apply(doc_id, caller_open_id)
   │  ├─ parse(text) → ParsedAction | None（前缀匹配）
   │  ├─ /replan <tid> <note>   → description 追加 + version bump
   │  ├─ /revise <tid> <desc>   → description 覆盖 + version bump
   │  ├─ /add-step <tid> <tool> → steps_json 追加 + version bump
   │  └─ 执行成功 → comment.processed_at 打标（幂等防重）
   ├─ VersionDiffService.diff(template_id, v_a, v_b)
   │  └─ TemplateVersionRepo 两版本加载 → 块对齐 → diff dict
   ├─ TagService（attach / detach / list_tags / find_by_tag）
   └─ FavoriteService（favorite / unfavorite / list_favorites）
```

**新增模块**：

| 文件 | 职责 |
|---|---|
| `persistence/repositories/comment_repo.py` | comments 表 CRUD（幂等 upsert / pending 列表 / 打标） |
| `persistence/repositories/template_tag_repo.py` | template_tags CRUD |
| `persistence/repositories/template_favorite_repo.py` | template_favorites CRUD |
| `orchestrator/templates/comment_sync_service.py` | 拉取 + 展平 + upsert + 统计 |
| `orchestrator/templates/comment_action_service.py` | 前缀解析 + owner 校验 + 执行 + 打标 |
| `orchestrator/templates/diff_service.py` | 块级结构化 diff + IM 渲染 |
| `orchestrator/templates/tag_service.py` | 标签服务 |
| `orchestrator/templates/favorite_service.py` | 收藏服务 |

**升级模块**：

| 文件 | 变更 |
|---|---|
| `persistence/models.py` | +`CommentRow` +`TemplateTagRow` +`TemplateFavoriteRow`（3 新表，共 16 张） |
| `gateway/app.py` | +7 路由（sync / stored / apply-actions / diff / tags / by-tag / favorites） |
| `orchestrator/app.py` | +`process_phase8()` + 6 IM 指令 |

---

## 3. 评论持久化（CommentSyncService）

### 3.1 目标

Phase 7 fetch-only 的评论在重启后丢失，且无法做"未处理评论"扫描。Phase 8 把评论落 `comments` 表，作为本地快照缓存。

### 3.2 数据流

```
用户："/comments-sync doc_1"
  ↓ process_phase8
  ↓ CommentSyncService.sync(doc_id="doc_1")
  ↓ CommentClient.list_comments(doc_id)（Phase 7 复用，含 rate_limit）
  ↓ 展平：root comment → 1 行；每条 reply → 1 行（parent_comment_id 关联）
  ↓ CommentRepo.upsert_many（comment_id 幂等：存在则更新 text/resolved/synced_at）
  ↓ 返回 {"fetched": N, "new": n1, "updated": n2}
  ↓ IM 回复："已同步 doc_1 评论：拉取 5，新增 2，更新 3"
```

### 3.3 数据模型（CommentRow）

| 字段 | 类型 | 说明 |
|---|---|---|
| comment_id | String PK | 飞书 comment_id（reply 用 reply_id） |
| doc_id | String, index | 所属文档 |
| block_id | String, nullable | 锚定块 |
| user_id | String | 评论作者 open_id |
| user_name | String | 显示名 |
| text | Text | 正文（指令前缀解析的输入） |
| is_reply | bool | 是否回复 |
| parent_comment_id | String, nullable | 父评论 |
| resolved | bool | 飞书侧是否已解决 |
| created_at | DateTime | 飞书侧创建时间（同步时写入） |
| synced_at | DateTime | 最近同步时间 |
| processed_at | DateTime, nullable | 动作执行打标（null = 未处理） |

### 3.4 同步语义

- **幂等**：重复 sync 同一评论不产生新行，只更新 `text` / `resolved` / `synced_at`
- **增量感知**：`processed_at` 不被 sync 覆盖（已执行动作的评论保持已处理，防重放）
- **无删除**：飞书侧删除的评论本地保留（快照语义）；Phase 9 可考虑 tombstone

### 3.5 查询接口（CommentRepo）

| 方法 | 用途 |
|---|---|
| `upsert_many(items)` | 批量幂等落库 |
| `list_by_doc(doc_id)` | 全量（stored API） |
| `list_pending(doc_id)` | `processed_at IS NULL` 且文本含指令前缀的候选 |
| `mark_processed(comment_id)` | 动作执行后打标 |

---

## 4. 评论触发动作（CommentActionService）

### 4.1 目标

导师/协作者在飞书 doc 评论中写指令 → owner 同步后在 IM 中显式 apply → agent 修改模板并自动 version bump。这是"评论驱动科研闭环"的核心。

### 4.2 指令语法（前缀约定）

| 指令 | 语法 | 语义 |
|---|---|---|
| replan | `/replan <template_id> <note>` | description 末尾追加 `[replan by <user>] <note>`，version bump |
| revise | `/revise <template_id> <desc>` | description 整体覆盖为 `<desc>`，version bump |
| add-step | `/add-step <template_id> <tool_name>` | subplan 模板 steps_json 追加 `{"tool": <tool>, "args": {}}`，version bump |

解析规则：

- 正文必须以指令前缀开头（`text.startswith("/replan ")` 等）；前缀后按空白切分
- 不匹配任何前缀 → 非动作评论（普通评论），apply 时跳过
- 参数不足 → 解析失败，apply 返回该条失败原因（不中断其余评论）

### 4.3 执行流程

```
用户："/comment-apply doc_1"
  ↓ CommentActionService.apply(doc_id="doc_1", caller_open_id="ou_owner")
  ↓ list_pending(doc_1) → [c1: "/replan t1 增加对照组", c2: "写得好"]
  ↓ 逐条 parse：
  │   c1 → ParsedAction(kind="replan", template_id="t1", payload="增加对照组")
  │   c2 → None（跳过，不打标）
  ↓ 对 c1：
  │   template_repo.get("t1") → 存在 & owner==caller → 执行
  │   ├─ 修改 description / steps_json（template_repo.upsert）
  │   ├─ VersionService.on_template_upsert（version bump，hard cap 10）
  │   └─ comment_repo.mark_processed(c1)
  ↓ 返回 {"applied": 1, "skipped": 1, "failed": 0, "details": [...]}
  ↓ IM 回复："已应用 1 条：replan t1；跳过 1 条普通评论"
```

### 4.4 权限与幂等

- **权限**：apply 的 caller 必须是每个目标模板的 owner；不是则该条 failed（reason=not_owner，PermissionError），不影响其他评论
- **幂等**：`processed_at` 打标后不再进入 pending 列表；重复 apply 无副作用
- **add-step 类型校验**：目标模板必须 `type == 'subplan'`（有 steps_json）；block 模板 → failed（reason=not_subplan）
- **版本链**：所有动作统一走 Phase 6 `VersionService.on_template_upsert`，版本历史可回滚（`/template-rollback`），hard cap 10 自动删最旧

### 4.5 与 Phase 7 的关系

- Phase 7 `/comments <doc_id>`（fetch + render）保留不动；Phase 8 新增 `/comments-sync`（落库）与 `/comment-apply`（执行）
- CommentClient 完全复用（含 rate_limiter）

---

## 5. 版本 diff（VersionDiffService）

### 5.1 目标

Phase 6 版本系统只有回滚，无法回答"两个版本改了什么"。Phase 8 提供块级结构化 diff，与 `/template-rollback` 组成完整的版本工作流。

### 5.2 diff 算法（块对齐）

```
diff(template_id, v_a, v_b):
  va, vb = version_repo.get_by_version(...) × 2
  blocks_a = json.loads(va.blocks_json or "[]")   # [{type, ...}, ...]
  blocks_b = json.loads(vb.blocks_json or "[]")
  steps_a = json.loads(va.steps_json or "[]")     # subplan 模板
  steps_b = json.loads(vb.steps_json or "[]")
```

**对齐策略**（同 type 连续段内按序对齐，LCS 简化版）：

1. 按 `type` 分组配对：同 type 的块按出现顺序一一配对
2. v_b 中配对成功的块 → 比较 dict 键集 → 有键值差异 → `changed`（记录差异字段名）
3. v_b 中未配对 → `added`；v_a 中未配对 → `removed`
4. steps 同理（按 `tool` 键配对）

**输出结构**：

```python
{
  "template_id": "t1", "v_a": 2, "v_b": 3,
  "blocks": {
    "added":   [{"index": 3, "type": "code"}],
    "removed": [{"index": 1, "type": "quote"}],
    "changed": [{"index": 2, "type": "heading", "fields": ["text"]}],
  },
  "steps": {"added": [...], "removed": [...], "changed": [...]},
  "meta": {"name_changed": true, "description_changed": false},
}
```

### 5.3 IM 渲染

```
模板 t1 版本 2 → 3 diff：
+ [3] code
- [1] quote
~ [2] heading (字段: text)
~ name: "std" → "std-v2"
steps: + 1 / - 0 / ~ 0
```

### 5.4 边界

- v_a == v_b → 空 diff（added/removed/changed 全空）
- 版本不存在 → ValueError（404）
- blocks_json 为 null 的 subplan 模板 → blocks diff 为空，steps diff 正常

---

## 6. 标签与收藏（TagService / FavoriteService）

### 6.1 目标

Phase 7 公共模板只能靠名称模糊搜索。Phase 8 增加标签（分类维度）与收藏（个人维度），公共库可用性闭环。

### 6.2 数据模型

**TemplateTagRow（template_tags，多对多）**：

| 字段 | 类型 |
|---|---|
| tag_id | String PK |
| template_id | String, index |
| tag | String, index（归一化小写，去首尾空白） |
| created_by | String |
| created_at | DateTime |
| **约束** | unique (template_id, tag) |

**TemplateFavoriteRow（template_favorites，user↔template）**：

| 字段 | 类型 |
|---|---|
| favorite_id | String PK |
| template_id | String, index |
| user_open_id | String, index |
| created_at | DateTime |
| **约束** | unique (template_id, user_open_id) |

### 6.3 服务语义

| 操作 | 语义 | 校验 |
|---|---|---|
| `attach(template_id, tag, caller)` | 打标签 | caller 是 owner；模板存在且未归档；重复 attach 幂等（静默） |
| `detach(template_id, tag, caller)` | 摘标签 | 同上；不存在静默 |
| `list_tags(template_id)` | 列标签 | 任意人可读 |
| `find_by_tag(tag)` | 按标签查模板 | 返回未归档模板行 |
| `favorite(template_id, caller)` | 收藏 | 模板存在；任意 scope（public/user/chat 均可）；幂等 |
| `unfavorite(template_id, caller)` | 取消收藏 | 幂等 |
| `list_favorites(user_open_id)` | 我的收藏 | 按时间倒序 |

### 6.4 与搜索的关系

`find_by_tag` 是独立入口，不并入 Phase 7 `TemplateSearchService`（trigram 搜索 name/description）。两者可在客户端组合使用（先按标签过滤再搜索）。tsvector 统一检索推迟 Phase 9。

---

## 7. 数据模型汇总（ORM 变更）

```python
# === Phase 8 ===  models.py 追加（总表数 13 → 16）

class CommentRow(Base):
    __tablename__ = "comments"
    comment_id: Mapped[str] = mapped_column(String, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    block_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    user_name: Mapped[str] = mapped_column(String, default="", nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_reply: Mapped[bool] = mapped_column(default=False, nullable=False)
    parent_comment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 唯一约束：comment_id 天然唯一（飞书侧生成）

class TemplateTagRow(Base):
    __tablename__ = "template_tags"
    tag_id: Mapped[str] = mapped_column(String, primary_key=True)
    template_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    # UniqueConstraint("template_id", "tag")

class TemplateFavoriteRow(Base):
    __tablename__ = "template_favorites"
    favorite_id: Mapped[str] = mapped_column(String, primary_key=True)
    template_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_open_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    # UniqueConstraint("template_id", "user_open_id")
```

迁移：沿用项目现状（`Base.metadata.create_all`，开发期 SQLite/PG 双兼容，见 models.py 头注）。

---

## 8. API 与 IM 指令

### 8.1 FastAPI 新路由（7 条）

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/comments/{doc_id}/sync` | — | `{fetched, new, updated}` |
| GET | `/comments/{doc_id}/stored` | `block_id?` | `{comments: [comment_id...]}` |
| POST | `/comments/{doc_id}/apply-actions` | body `{caller_open_id}` | `{applied, skipped, failed, details}` |
| GET | `/templates/{template_id}/diff` | `?v_a=&v_b=` | diff dict（§5.2） |
| POST | `/templates/{template_id}/tags` | body `{tag, caller_open_id}` | `{ok}` |
| DELETE | `/templates/{template_id}/tags` | `?tag=&caller_open_id=` | `{ok}` |
| GET | `/templates/by-tag/{tag}` | — | `{templates: [id...]}` |
| POST | `/templates/{template_id}/favorite` | body `{caller_open_id}` | `{ok}` |
| DELETE | `/templates/{template_id}/favorite` | `?caller_open_id=` | `{ok}` |
| GET | `/templates/favorites/{user_open_id}` | — | `{templates: [id...]}` |

> 实际为 10 个端点（sync/stored/apply/diff/tags-POST/tags-DELETE/by-tag/fav-POST/fav-DELETE/favorites），7 组资源。

错误码：403（not owner）/ 404（模板或版本不存在） / 400（参数错误） / 503（service 未配置）。

### 8.2 IM 指令（process_phase8，6 条）

| 指令 | 用法 | 动作 |
|---|---|---|
| `/comments-sync` | `/comments-sync <doc_id>` | 同步评论落库 |
| `/comment-apply` | `/comment-apply <doc_id>` | 应用 pending 评论动作 |
| `/template-diff` | `/template-diff <tid> <v_a> <v_b>` | 渲染 diff 文本 |
| `/template-tag` | `/template-tag <tid> <tag>` | 打标签 |
| `/template-favorite` | `/template-favorite <tid>` | 收藏 |
| `/template-favorites` | `/template-favorites` | 我的收藏列表 |

未匹配 → 回落 `process_phase7`（链式，与 Phase 5→6→7 一致）。

---

## 9. 测试策略

### 9.1 测试矩阵（计划 48，允许 +5 浮动）

| 层 | 文件 | 计划数 | 覆盖 |
|---|---|---|---|
| 单元 | `test_comment_repo.py` | 4 | insert / 幂等 update / list_pending / mark_processed |
| 单元 | `test_comment_sync_service.py` | 4 | 展平落库 / 幂等重同步 / 空同步 / 统计返回 |
| 单元 | `test_comment_action_service.py` | 7 | 三前缀解析 / 无前缀 None / owner 执行 + 打标 / 非 owner 拒绝 / add-step 类型校验 |
| 单元 | `test_diff_service.py` | 5 | added / removed / changed 字段 / 同版本空 / IM 渲染 |
| 单元 | `test_template_tag_repo.py` | 3 | 唯一约束幂等 / detach / find_by_tag |
| 单元 | `test_template_favorite_repo.py` | 3 | 幂等 / unfavorite / list_by_user |
| 单元 | `test_tag_service.py` | 3 | attach 权限 / detach / list_tags |
| 单元 | `test_favorite_service.py` | 3 | favorite / unfavorite / list_favorites |
| 单元 | `test_orchestrator_phase8.py` | 1 | process_phase8 smoke |
| 集成 | `test_comments_phase8_api.py` | 3 | sync / stored / apply 路由 |
| 集成 | `test_templates_phase8_api.py` | 7 | diff / tags×2 / by-tag / favorite×2 / favorites |
| E2E | `test_e2e_phase8_e1_e8.py` | 8 | E1-E8 场景（下） |

### 9.2 E2E 场景（E1-E8）

| # | 场景 | 断言核心 |
|---|---|---|
| E1 | 导师评论 `/replan` → sync → apply | description 追加、version +1、processed_at 打标 |
| E2 | 评论 `/add-step` → sync → apply | steps_json +1 步、version +1 |
| E3 | 非 owner apply | failed=1、reason=not_owner、模板未变 |
| E4 | 重复 apply（幂等） | 第二次 applied=0 |
| E5 | `/revise` 前后两版本 diff | changed 含 description、渲染含 `~` |
| E6 | tag → by-tag 检索 | find_by_tag 命中 |
| E7 | favorite → favorites 列表 | 命中且幂等 |
| E8 | root+reply 展平 + block_id 锚定 | 2 行落库、parent_comment_id 关联、按 block 过滤 |

### 9.3 回归门

- Phase 7 累计 389 测试 **0 回归**
- 新增 ≥ 48 → 总数 ≥ 437

---

## 10. 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| 评论指令被恶意执行（非 owner 评论） | 中 | apply 权限锚定 owner；评论作者仅建议，执行权在 owner（ADR-0023） |
| 同一评论重复执行 | 中 | processed_at 打标 + pending 过滤；sync 不覆盖 processed_at |
| diff 对齐算法在重排序时误报 changed | 中 | 同 type 按序配对为 MVP；全量 LCS 推迟；diff 仅展示用途不驱动回滚 |
| 评论量大时 sync 慢 | 低 | rate_limiter 复用；单 doc 评论量预期 < 100；分页拉取推迟 |
| tags 表膨胀（垃圾标签） | 低 | 仅 owner 可打标；归一化小写；admin 清理推迟 Phase 9 |
| 新表迁移遗漏 | 低 | create_all 自动建表；测试夹具全量建表验证 |

---

## 11. ADR 清单（Phase 8）

| ADR | 标题 | 决策 |
|---|---|---|
| 0019 | phase8-comment-sync-on-demand | 按需增量同步（非 webhook、非实时） |
| 0020 | phase8-comment-command-prefix | 指令前缀约定（非 LLM 意图） |
| 0021 | phase8-version-block-diff | 块级结构化 diff（非文本 unified diff） |
| 0022 | phase8-template-tag-favorite | 独立关联表（非 JSON 字段） |
| 0023 | phase8-comment-action-auth | owner 显式 apply（非评论作者自动触发） |

---

## 12. 后续动作与阶段门

**Phase 8 实施前**：

1. ✅ 本 spec 14 章用户确认（A,A,A,A,A 已批）
2. ⏭️ 写 5 个 ADR（0019-0023）
3. ⏭️ 写 Phase 8 plan（writing-plans skill）
4. ⏭️ 用户评审 plan + ADR
5. ⏭️ Inline 实施（沿用 Phase 3-7 节奏）

**Phase 9 候选范围**：

| 模块 | 候选 |
|---|---|
| 评论 | webhook 实时推送 / agent 回复评论 / 评论 @ 提及路由 |
| 检索 | tsvector 统一检索（标签+全文+收藏融合排序） |
| 模板 | merge / branch / conflict / 跨 fork 更新同步 |
| 工具 | 工具 ACL / 在线热重载 |
| 领域 | AlphaFold / 多序列比对 / 本地 DB 自动同步 |
| 运维 | 评论分页拉取 / admin 标签清理 / tombstone |
| 历史欠账 | Phase 2-7 推迟项 |

**Phase 8 阶段门**：

| 阶段门 | 标准 |
|---|---|
| 设计门 | 本 spec 用户确认（✅ 2026-08-26 A,A,A,A,A） |
| ADR 门 | 5 个 ADR 用户可评审 |
| 实施门 | plan 用户确认后 inline 实施 |
| 测试门 | ≥ 48 新测试通过；389 老测试 0 回归 |

---

## 13. 与 Phase 7 的接口契约（不变项）

以下 Phase 7 接口**签名与语义完全不变**，Phase 8 只做加法：

- `CommentClient.list_comments / list_block_comments`（复用，不改动）
- `CommentService.fetch_thread`（fetch-only 渲染保留）
- `TemplateSearchService.search`（不变，by-tag 是独立入口）
- `PublicTemplateService` / `ForkService`（不变）
- `templates` 表既有字段（scope / chat_id / lineage_template_id 不动）
- 既有 21 条路由 + process_phase7 指令链

---

## 14. 实施结果（已交付）

### 测试与提交

- **全量回归：445 passed / 0 failed**（Phase 7 基线 389 + 新增 56，0 回归）✅
- commit：`feat(phase8): implement comment-driven loop (sync + actions + diff + tags/favorites)`

### 实际测试数

| 模块 | 计划 | 实际 |
|---|---|---|
| CommentRepo | 4 | **4** |
| TagRepo + FavoriteRepo | 6 | **6** |
| CommentSyncService | 4 | **4** |
| CommentActionService（含 parse） | 7 | **8** |
| VersionDiffService | 5 | **7** |
| TagService + FavoriteService | 6 | **6** |
| Orchestrator phase8 smoke | 1 | **2** |
| 评论 API（sync/stored/apply） | 3 | **3** |
| 模板 API（diff/tags/favorites） | 7 | **8** |
| E2E E1-E8 | 8 | **8** |
| **新增小计** | 51 | **56** |
| Phase 7 累计 | 389 | 389 |
| **总计** | 440 | **445** ✅ |

### 累计测试曲线

```
Phase 1:86 → 2:147 → 3:216 → 4:230 → 5:278 → 6:336 → 7:389 → 8:445
                                                   +53        +56
```

### 新增/修改文件清单

```
新增（源码 8）：
- persistence/repositories/comment_repo.py          # 评论幂等 upsert + pending + 打标
- persistence/repositories/template_tag_repo.py     # 标签 CRUD
- persistence/repositories/template_favorite_repo.py# 收藏 CRUD
- orchestrator/templates/comment_sync_service.py    # 拉取+展平+落库
- orchestrator/templates/comment_action_service.py  # 前缀解析 + owner apply
- orchestrator/templates/diff_service.py            # 块级结构化 diff + IM 渲染
- orchestrator/templates/tag_service.py             # 标签服务（owner + 归一化）
- orchestrator/templates/favorite_service.py        # 收藏服务

新增（测试 10）：
- tests/unit/test_comment_repo.py
- tests/unit/test_template_tag_favorite_repo.py
- tests/unit/test_comment_sync_service.py
- tests/unit/test_comment_action_service.py
- tests/unit/test_diff_service.py
- tests/unit/test_tag_favorite_service.py
- tests/unit/test_orchestrator_phase8.py
- tests/integration/test_comments_phase8_api.py
- tests/integration/test_templates_phase8_api.py
- tests/integration/test_e2e_phase8_e1_e8.py

修改：
- persistence/models.py        # +CommentRow +TemplateTagRow +TemplateFavoriteRow（13→16 表）
- gateway/app.py               # +5 service 注入 + 10 路由（sync/stored/apply/diff/tags×2/by-tag/favorite×2/favorites）
- orchestrator/app.py          # +process_phase8 + 6 IM 指令
```

### 交付能力（评论驱动闭环全链路）

导师在飞书 doc 评论 `/replan t1 增加对照组` → 用户 IM `/comments-sync <doc_id>` 落库 → `/comment-apply <doc_id>` 执行（owner 权限 + version bump + processed 打标）→ `/template-diff t1 1 2` 查看变更 → `/template-rollback t1 1` 可回滚。配套 `/template-tag` / `/template-favorite` / `/template-favorites` 完成公共库可用性闭环。
