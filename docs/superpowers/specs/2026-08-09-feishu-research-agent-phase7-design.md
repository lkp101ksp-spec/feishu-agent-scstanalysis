# 飞书科研闭环 Agent — Phase 7 设计稿

> 日期：2026-08-09
> 状态：Phase 7 设计稿（待实施）
> 范围：协作评论 + 模板搜索 + 公共模板 + 审核流 + fork
> 推迟：评论持久化 / 评论触发 agent行为 / 模板标签 / 富文本 diff / 工具 ACL / AlphaFold / Phase 2-5 推迟项（Phase 8）
> 前置：[Phase 1](./2026-08-08-feishu-research-agent-design.md) / [Phase 2](./2026-08-08-feishu-research-agent-phase2-design.md) / [Phase 3](./2026-08-09-feishu-research-agent-phase3-design.md) / [Phase 4 MVP](./2026-08-09-feishu-research-agent-phase4-mvp-design.md) / [Phase 5](./2026-08-09-feishu-research-agent-phase5-design.md) / [Phase 6](./2026-08-09-feishu-research-agent-phase6-design.md)
> 后置：Phase 7 实施计划 [`../plans/2026-08-09-feishu-research-agent-phase7.md`](../plans/2026-08-09-feishu-research-agent-phase7.md)（待写）

---

## 1. 范围与目标

### 1.1 Phase 7 主题

在 Phase 6 富文本与模板市场基础上，扩展**协作能力**（评论 + thread）+ **检索能力**（搜索）+ **公共模板**（审核流）+ **复用增强**（fork）。

### 1.2 范围（4 子系统）

| 模块 | Phase 7 内容 | Phase 7 不做（推迟）|
|---|---|---|
| **协作评论** | 飞书 doc comment API；thread；@；按 block_id 锚定 | 评论持久化 / 评论触发 agent行为 |
| **模板搜索** | PG trigram + LIKE；name / description / owner 模糊匹配；pagination；filter by scope | 全文检索 / 标签 / ES |
| **公共模板 + 审核** | scope='public'；admin approval；approve/reject + reason | 软删除 / 版本对比 |
| **模板 fork** | lineage 字段；用户从 public fork 到自己；编辑独立；可再次提交 | merge / branch / conflict |

### 1.3 关键约束（继承 Phase 1-6）

- **协作评论**不持久化到 Agent（仅 fetch + render）；用户已在飞书 doc 看到评论
- **搜索**不引入额外组件（PG 内置）
- **公共模板**需 owner 提交 → admin 通过 → 公共可见；拒绝时记录 reason
- **fork**复制模板内容到 owner 私有；lineage 记录来源；不能反向 merge
- 4 个子系统**互不破坏** Phase 6 私有 + 群聊共享

### 1.4 关键决策（用户已批）

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| 1 | Phase 7 架构 | 方案 A（分模块并行）| 与 Phase 6 节奏一致；回归隔离 |
| 2 | 评论深度 | A：飞书原生 + thread + @ | 不持久化；与飞书 UI 一致 |
| 3 | 搜索方式 | A：PG trigram + LIKE | 无额外依赖；MVP 足够 |
| 4 | 公共模板 | A：审核流 + approve/reject | 风险可控；保留拒绝原因 |
| 5 | fork 范围 | A：仅 fork + 修改 | 与 Phase 6 公共模板紧密衔接 |
| 6 | 评论是否持久化 | 不持久化 | 避免与飞书 doc 数据冲突 |
| 7 | 评论 thread | 支持 reply | 飞书原生支持 |
| 8 | 搜索结果数 | 默认 20，可调 | 防 DB 压力 |
| 9 | 公共模板删除 | soft delete（archived_at）| 保留历史 |
| 10 | fork 来源 | 仅 public 可 fork | 用户私有 / 群聊共享不 fork |

### 1.5 不做清单（明确边界）

- 评论持久化（评论只读模式）
- 评论触发 agent行为
- 全文检索（PG tsvector）
- 模板标签 / 收藏
- 富文本 diff / 撤销
- 工具 ACL
- 工具在线热重载
- AlphaFold / 多序列比对 / 本地数据库自动同步
- 模板 merge / branch / conflict
- 公共模板版本对比
- Phase 2-6 推迟项

---

## 2. 总体架构

```
FastAPI（Phase 6 不变）
 └─ Orchestrator.process_phase7(incoming)
   ├─ Planner / Scheduler / LocalExecutor（不变）
   ├─ CommentService.fetch_thread(doc_id, block_id)
   │  └─ CommentLarkClient → 飞书 doc comment API
   ├─ TemplateSearchService.search(query, scope, owner_id, ...)
   │  └─ PG trigram + LIKE on TemplateRow
   ├─ PublicTemplateService
   │  ├─ submit_for_review(template_id)         # scope='public_pending'
   │  ├─ approve(template_id, admin_id, note)   # scope='public'
   │  └─ reject(template_id, admin_id, reason)  # 保持 scope='user'
   └─ ForkService.fork_from_public(...)
      └─ 复制 TemplateRow + lineage 字段
```

**新增模块**：
- `feishu_adapter/comment_client.py`：飞书 doc comment API 包装
- `orchestrator/templates/search_service.py`：PG trigram 搜索
- `orchestrator/templates/public_service.py`：公共模板 + 审核
- `orchestrator/templates/fork_service.py`：fork 流程
- `orchestrator/templates/audit_service.py`：审核日志（Phase 7 简化：仅写 audit）

**升级模块**：
- `templates` 表：+lineage 字段、+scope='public'/'public_pending'/'user'/'chat'
- `template_audit` 表（新增）：公共模板审核日志
- `FastAPI`：+`/comments` `/search` `/admin/templates/review` `/fork` 路由
- `Orchestrator`：process_phase7 + IM 指令

---

## 3. 协作评论（飞书 doc comment API）

### 3.1 目标

集成飞书 doc comment API；按 block_id 锚定；展示 thread；支持 reply。

### 3.2 数据流

```
用户："查询 doc_1 的评论"
  ↓ process_phase7
  ↓ CommentService.fetch_thread(doc_id="doc_1")
  ↓ 飞书 open_api: docx.comment.list?doc_id=doc_1
  ↓ 返回 comment list（thread root + replies）
  ↓ TemplateEngine.render_blocks_to_text 渲染为 IM 文本
  ↓ IM 回复："评论列表：\n1. <user>: <text>\n..."
```

### 3.3 飞书 doc comment API

- **列出评论**：`POST /open-apis/docx/v1/documents/{document_id}/comments`
- **回复评论**：`POST /open-apis/docx/v1/documents/{document_id}/comments/{comment_id}/replies`
- **锚定 block_id**：`block_id` 字段标识 comment 位置

Phase 7 简化：
- 仅支持 GET（不写评论到飞书）
- 仅展示 thread root + replies
- 不持久化到 Agent（避免双系统）

### 3.4 CommentClient

```python
class CommentClient:
    """Phase 7: 飞书 doc comment API 客户端。"""
    
    def __init__(self, *, base_url: str, api_token: str,
                 rate_limiter: RateLimiter):
        self.base_url = base_url
        self.api_token = api_token
        self.rate_limiter = rate_limiter
    
    def list_comments(self, *, doc_id: str) -> list[dict]:
        """Phase 7: 列出 doc 所有评论（thread + replies）。"""
        url = f"{self.base_url}/open-apis/docx/v1/documents/{doc_id}/comments"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        self.rate_limiter.wait()
        resp = httpx.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("items", [])
    
    def list_block_comments(self, *, doc_id: str,
                              block_id: str) -> list[dict]:
        """Phase 7: 列出指定 block 的评论（过滤）。"""
        all_comments = self.list_comments(doc_id=doc_id)
        return [c for c in all_comments if c.get("block_id") == block_id]
```

### 3.5 CommentService

```python
class CommentService:
    """Phase 7: 评论聚合 + IM 文本渲染。"""
    
    def __init__(self, comment_client: CommentClient,
                 template_engine: TemplateEngine):
        self.comment_client = comment_client
        self.template_engine = template_engine
    
    def fetch_thread(self, *, doc_id: str,
                     block_id: str | None = None) -> str:
        """Phase 7: 获取评论 thread 并渲染为 IM 文本。"""
        if block_id:
            comments = self.comment_client.list_block_comments(
                doc_id=doc_id, block_id=block_id)
        else:
            comments = self.comment_client.list_comments(doc_id=doc_id)
        if not comments:
            return "（无评论）"
        lines = ["评论列表："]
        for c in comments:
            user = c.get("user_name", "匿名")
            text = c.get("text", "")
            lines.append(f"- {user}: {text}")
            for reply in c.get("replies", []):
                r_user = reply.get("user_name", "匿名")
                r_text = reply.get("text", "")
                lines.append(f"  ↳ {r_user}: {r_text}")
        return "\n".join(lines)
```

### 3.6 IM 指令

```
/comments <doc_id> [block_id]
  → 列出 doc（或指定 block）的评论
```

### 3.7 速率限制

飞书 doc comment API 通常 3 req/s；复用 Phase 6 RateLimiter。

---

## 4. 模板搜索（PG trigram + LIKE）

### 4.1 目标

用户可按 name / description / owner 搜索模板；支持 scope 过滤；pagination。

### 4.2 技术选型

- **PG trigram**：`pg_trgm` extension；按 name trigram 索引
- **LIKE**： 简单 LIKE 子句（中文 / 数字 / 混合）
- **复合查询**：name + description + owner_open_id

**Phase 7 简化**：
- 启用 `pg_trgm` extension（迁移）
- 在 `name` 列加 GIN trigram 索引
- 搜索 = `name ILIKE '%query%' OR description ILIKE '%query%'`
- 限制：默认 20 条；可调

### 4.3 SQLAlchemy migration

```python
# migrations/versions/0007_phase7_*.py

def upgrade():
    # 启用 trgm 扩展
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # GIN trigram 索引
    op.create_index(
        "ix_templates_name_trgm",
        "templates",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
```

### 4.4 SearchService

```python
class TemplateSearchService:
    """Phase 7: 模板搜索（PG trigram + LIKE）。"""
    
    def __init__(self, template_repo: TemplateRepo):
        self.template_repo = template_repo
    
    def search(
        self, *,
        query: str,
        scope: str | None = None,
        owner_open_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[TemplateRow]:
        """Phase 7: 按 query 模糊匹配；可选 scope + owner 过滤。"""
        return self.template_repo.search(
            query=query, scope=scope,
            owner_open_id=owner_open_id,
            limit=limit, offset=offset,
        )
```

### 4.5 TemplateRepo.search

```python
def search(
    self, *,
    query: str,
    scope: str | None = None,
    owner_open_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[TemplateRow]:
    q = self.session.query(TemplateRow).filter(
        TemplateRow.archived_at.is_(None)
    )
    if query:
        # PG trigram 匹配 + ILIKE 兜底
        like = f"%{query}%"
        q = q.filter(
            (TemplateRow.name.ilike(like)) |
            (TemplateRow.description.ilike(like))
        )
    if scope:
        q = q.filter(TemplateRow.scope == scope)
    if owner_open_id:
        q = q.filter(TemplateRow.owner_open_id == owner_open_id)
    return q.order_by(TemplateRow.updated_at.desc()) \
             .limit(limit).offset(offset).all()
```

### 4.6 FastAPI 路由

```python
GET /templates/search?q=<query>&scope=<public|user|chat>&owner_open_id=<id>&limit=20&offset=0
```

---

## 5. 公共模板 + 审核

### 5.1 目标

私有模板可提交审核；管理员通过后变公共；拒绝时记录原因。

### 5.2 数据模型扩展

```sql
-- templates 表 scope 字段扩展
-- 'user' | 'chat' | 'public_pending' | 'public'

ALTER TABLE templates ALTER COLUMN scope SET DEFAULT 'user';
-- 已有 'user' / 'chat'；新增 'public_pending' / 'public'

-- 新增审核日志表
CREATE TABLE template_audit (
    audit_id      TEXT PRIMARY KEY,
    template_id   TEXT NOT NULL,
    action        TEXT NOT NULL,  -- 'submit' | 'approve' | 'reject'
    actor_open_id TEXT NOT NULL,
    reason        TEXT,
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX ix_template_audit_template_id
    ON template_audit (template_id, created_at DESC);
```

### 5.3 审核流

```
1. 用户 A 创建模板（scope='user'）
2. 用户 A 发 /template-submit-public <id>：
   - 把 scope → 'public_pending'
   - 写 audit: action='submit', actor=A
3. admin 发 /admin/templates/review <id> approve:
   - 把 scope → 'public'
   - 写 audit: action='approve', actor=admin
4. 或 admin 发 /admin/templates/review <id> reject <reason>:
   - 保持 scope='user'
   - 写 audit: action='reject', actor=admin, reason=<reason>
```

### 5.4 IM 指令

```
/template-submit-public <id>      # owner 提交
/admin/templates/review <id> approve   # admin 通过
/admin/templates/review <id> reject <reason>  # admin 拒绝
/templates/public                   # 列出所有公共模板
```

### 5.5 PublicService

```python
class PublicTemplateService:
    """Phase 7: 公共模板 + 审核流。"""
    
    def __init__(self, template_repo, audit_repo, admin_user_ids: set[str]):
        self.template_repo = template_repo
        self.audit_repo = audit_repo
        self.admin_user_ids = admin_user_ids
    
    def submit_for_review(self, *, template_id: str,
                           actor_open_id: str) -> None:
        tpl = self.template_repo.get(template_id)
        if tpl is None or tpl.archived_at:
            raise ValueError(f"template {template_id} not found")
        if tpl.owner_open_id != actor_open_id:
            raise PermissionError("not owner")
        if tpl.scope not in {"user", "chat"}:
            raise ValueError(f"template scope is {tpl.scope}, cannot submit")
        self.template_repo.upsert(
            template_id=template_id,
            owner_open_id=tpl.owner_open_id,
            name=tpl.name, type_=tpl.type,
            blocks_json=tpl.blocks_json, steps_json=tpl.steps_json,
            description=tpl.description,
            scope="public_pending", chat_id=None,
        )
        self.audit_repo.write(
            audit_id=new_ulid(),
            action="template_submit_public",
            actor_type="user", actor_id=actor_open_id,
            target_type="template", target_id=template_id,
            detail={},
        )
    
    def approve(self, *, template_id: str, actor_open_id: str,
                note: str = "") -> None:
        if actor_open_id not in self.admin_user_ids:
            raise PermissionError("not admin")
        tpl = self.template_repo.get(template_id)
        if tpl is None:
            raise ValueError("not found")
        if tpl.scope != "public_pending":
            raise ValueError(f"template scope is {tpl.scope}, not pending")
        self.template_repo.upsert(
            template_id=template_id,
            owner_open_id=tpl.owner_open_id,
            name=tpl.name, type_=tpl.type,
            blocks_json=tpl.blocks_json, steps_json=tpl.steps_json,
            description=tpl.description,
            scope="public", chat_id=None,
        )
        self.audit_repo.write(
            audit_id=new_ulid(),
            action="template_approve_public",
            actor_type="admin", actor_id=actor_open_id,
            target_type="template", target_id=template_id,
            detail={"note": note},
        )
    
    def reject(self, *, template_id: str, actor_open_id: str,
                reason: str) -> None:
        if actor_open_id not in self.admin_user_ids:
            raise PermissionError("not admin")
        tpl = self.template_repo.get(template_id)
        if tpl is None:
            raise ValueError("not found")
        if tpl.scope != "public_pending":
            raise ValueError(f"template scope is {tpl.scope}, not pending")
        # 保持 scope='user'
        self.template_repo.upsert(
            template_id=template_id,
            owner_open_id=tpl.owner_open_id,
            name=tpl.name, type_=tpl.type,
            blocks_json=tpl.blocks_json, steps_json=tpl.steps_json,
            description=tpl.description,
            scope="user", chat_id=None,
        )
        self.audit_repo.write(
            audit_id=new_ulid(),
            action="template_reject_public",
            actor_type="admin", actor_id=actor_open_id,
            target_type="template", target_id=template_id,
            detail={"reason": reason},
        )
    
    def list_public(self, *, limit: int = 20,
                     offset: int = 0) -> list[TemplateRow]:
        return self.template_repo.list_by_scope(
            scope="public", limit=limit, offset=offset,
        )
```

### 5.6 TemplateRepo 升级

```python
def list_by_scope(self, *, scope: str,
                  limit: int = 20, offset: int = 0) -> list[TemplateRow]:
    return (
        self.session.query(TemplateRow)
        .filter_by(scope=scope, archived_at=None)
        .order_by(TemplateRow.updated_at.desc())
        .limit(limit).offset(offset).all()
    )
```

### 5.7 Admin 校验

Phase 7 简化：admin 列表由配置 `settings.admin_user_ids` 注入；生产可扩展到 RBAC。

---

## 6. 模板 fork

### 6.1 目标

用户从 public 模板 fork 到自己私有；编辑独立；lineage 记录来源。

### 6.2 数据模型扩展

```sql
ALTER TABLE templates ADD COLUMN lineage_template_id TEXT;
-- 指向原始 public 模板（仅 fork 时设置）
```

### 6.3 Fork 流程

```
1. 用户 A 发 /template-fork <id>（<id> 必须是 public scope）
2. ForkService.fork_from_public：
   - 读原始 public 模板
   - 校验 scope='public'
   - 创建新 TemplateRow：
     - template_id=new_ulid()
     - owner_open_id=A
     - scope='user'
     - lineage_template_id=<原始 id>
     - name = "{原始 name} (fork by A)"
     - blocks_json / steps_json 复制
3. 返回新 template_id
4. 用户 A 可独立编辑；lineage 不可改
```

### 6.4 IM 指令

```
/template-fork <public_id>
```

### 6.5 ForkService

```python
class ForkService:
    """Phase 7: fork public template to user-owned."""
    
    def __init__(self, template_repo):
        self.template_repo = template_repo
    
    def fork_from_public(
        self, *,
        source_template_id: str,
        actor_open_id: str,
    ) -> str:
        src = self.template_repo.get(source_template_id)
        if src is None or src.archived_at:
            raise ValueError(f"template {source_template_id} not found")
        if src.scope != "public":
            raise PermissionError(
                f"template scope is {src.scope}, only public can fork"
            )
        new_id = new_ulid()
        self.template_repo.upsert(
            template_id=new_id,
            owner_open_id=actor_open_id,
            name=f"{src.name} (fork by {actor_open_id})",
            type_=src.type,
            blocks_json=src.blocks_json,
            steps_json=src.steps_json,
            description=src.description,
            scope="user", chat_id=None,
            lineage_template_id=source_template_id,
        )
        return new_id
    
    def list_forks(self, source_template_id: str) -> list[TemplateRow]:
        return self.template_repo.list_by_lineage(source_template_id)
```

### 6.6 限制

- 仅 public 可 fork（私有 / 群聊共享不 fork）
- lineage 不可改（防止伪造来源）
- fork 不可再 fork（防止 chain 复杂度爆炸）—— Phase 7 简化：fork 的 scope='user'，不能 fork 自己的 fork（因为 user scope 不能 fork）

### 6.7 TemplateRepo 升级

```python
def list_by_lineage(self, lineage_template_id: str) -> list[TemplateRow]:
    return (
        self.session.query(TemplateRow)
        .filter_by(lineage_template_id=lineage_template_id,
                    archived_at=None)
        .all()
    )
```

---

## 7. PostgreSQL schema 增量

### 7.1 templates 表字段扩展

```sql
-- Phase 7
ALTER TABLE templates ALTER COLUMN scope SET DEFAULT 'user';
-- 新增合法值：'public_pending' / 'public'
ALTER TABLE templates ADD COLUMN lineage_template_id TEXT;
CREATE INDEX ix_templates_lineage ON templates (lineage_template_id)
    WHERE lineage_template_id IS NOT NULL;
```

### 7.2 新增表

```sql
-- 公共模板审核日志（§5）
CREATE TABLE template_audit (
    audit_id      TEXT PRIMARY KEY,
    template_id   TEXT NOT NULL,
    action        TEXT NOT NULL,  -- 'submit' | 'approve' | 'reject'
    actor_open_id TEXT NOT NULL,
    reason        TEXT,
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX ix_template_audit_template_id
    ON template_audit (template_id, created_at DESC);
```

### 7.3 索引（搜索性能）

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX ix_templates_name_trgm
    ON templates USING gin (name gin_trgm_ops);
```

### 7.4 迁移

新增 `migrations/versions/0007_phase7_*.py`。

---

## 8. 测试策略

### 8.1 测试矩阵

| 子系统 | 单元 | 集成 | 端到端 |
|---|---|---|---|
| CommentClient（mock API）| - | 5 | - |
| CommentService（fetch_thread + render）| 4 | - | - |
| TemplateSearchService | 4 | - | - |
| PublicTemplateService（submit/approve/reject/admin 校验）| 6 | - | - |
| ForkService（fork_from_public + 限制）| 4 | - | - |
| TemplateRepo.search + list_by_scope + list_by_lineage | 5 | - | - |
| 审核日志 | 2 | - | - |
| FastAPI 新路由（/comments /search /admin/templates/review /fork）| - | 7 | - |
| E1-E8 端到端 | - | - | 8 |
| **合计** | **25** | **12** | **8** |

### 8.2 E1-E8 端到端场景

| # | 场景 | 验证点 |
|---|---|---|
| E1 | 协作评论 fetch_thread（mock 飞书 doc comment API）| respx mock + Thread 渲染 |
| E2 | 评论按 block_id 过滤 | list_block_comments |
| E3 | 模板搜索（PG trigram + LIKE）| 多条件 query |
| E4 | 公共模板提交 → 审核通过 | scope 转换 + audit 写 |
| E5 | 公共模板提交 → 审核拒绝（带 reason）| 保持 scope='user' + audit |
| E6 | 非 admin 提交拒绝 | PermissionError |
| E7 | fork 公共模板 → 私有 | lineage 设置 + 内容复制 |
| E8 | fork 非公共模板 → 403 | PermissionError |

### 8.3 mock 策略

- **飞书 doc comment API**：respx mock（与 Phase 5 一致）
- **PG trigram**：SQLite 测试用 LIKE；生产 PG 单独验证（CI 跑一次）
- **审核流**：MagicMock audit_repo

### 8.4 覆盖率目标

| 模块 | 目标 |
|---|---|
| CommentService | ≥ 85% |
| PublicTemplateService | ≥ 90% |
| ForkService | ≥ 90% |
| TemplateSearchService | ≥ 85% |
| **Phase 7 新增整体** | ≥ 80% |

### 8.5 回归保证

- Phase 1-6 全部 336 测试**必须**继续通过
- Phase 6 templates.scope='user' / 'chat' **必须**继续兼容
- Phase 5 Block + Phase 6 富文本 14 类**必须**不破坏

---

## 9. Phase 7 不做（明确边界）

| 项 | 推迟到 | 原因 |
|---|---|---|
| 评论持久化（评论写本地表）| Phase 8 | MVP 不做双系统 |
| 评论触发 agent行为 | Phase 8 | MVP 不响应评论 |
| 全文检索（PG tsvector）| Phase 8 | MVP LIKE 足够 |
| 模板标签 / 收藏 | Phase 8 | MVP 不做 |
| 富文本 diff / 撤销 / inline math渲染 | Phase 8 | MVP 不做 |
| 模板 merge / branch / conflict | Phase 8 | MVP 仅 fork |
| 公共模板版本对比 | Phase 8 | MVP 不做 |
| 公共模板软删除 | Phase 8 | MVP 用 archived_at（复用）|
| 工具 ACL（hot_loader admin 校验）| Phase 8 | MVP 不做 |
| 工具在线热重载 | Phase 8 | MVP restart 后生效 |
| AlphaFold / 多序列比对 / 本地数据库自动同步 | Phase 8 | MVP 仅 BLAST |
| Phase 2-6 推迟项 | Phase 8 | 全推迟 |

---

## 10. 风险与决策

### 10.1 已识别风险

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 飞书 doc comment API 变更 / 限流 | respx mock 隔离；生产 3 req/s |
| R2 | PG trigram 扩展未启用 | migration check + error message |
| R3 | 公共模板审核流程被绕过 | admin_user_ids 校验 + audit |
| R4 | fork 链条无限延伸 | 仅 public 可 fork + 仅 user scope fork 后不可再 fork |
| R5 | lineage 伪造 | 仅 fork_service 设置 + 不暴露 update 接口 |
| R6 | 搜索结果过多导致 DB 压力 | limit 上限 100（默认 20）|
| R7 | 审核拒绝 reason 过长 | max length 500 chars |
| R8 | fork 后原始 public 模板删除 | 保留 archived_at；fork 不受影响（lineage 仅作引用）|
| R9 | 跨用户 fork 的 lineage 隐私 | lineage 可见（public 模板的 fork 关系公开）|

### 10.2 ADR（决策记录）待写

| # | ADR 主题 |
|---|---|
| 0015 | 协作评论不持久化（仅 fetch + render）|
| 0016 | 模板搜索用 PG trigram + LIKE |
| 0017 | 公共模板审核流（admin + audit + reason）|
| 0018 | fork 仅 public + lineage 不可改 |

---

## 11. ADR 清单（Phase 7）

待 Phase 7 实施前写 4 个 ADR：

1. **0015 phase7-comment-fetch-only**：飞书 doc comment API 集成 + 不持久化
2. **0016 phase7-template-search**：PG trigram + LIKE
3. **0017 phase7-public-template-review**：admin approval + reason
4. **0018 phase7-fork-public-only**：仅 fork public + lineage 不可改

---

## 12. 后续动作

**Phase 7 实施前**：

1. ✅ **写完本 spec**：12 章全部展开
2. ⏭️ **写 Phase 7 plan**（writing-plans skill）
3. ⏭️ **写 4 个 ADR**（决策记录）
4. ⏭️ **用户评审 plan + ADR**
5. ⏭️ **Inline 实施**（沿用 Phase 3-6 节奏）

**Phase 8 候选范围**：

| 模块 | Phase 8 候选 |
|---|---|
| 评论持久化 | 评论写本地表 + 评论触发 agent 重新规划 |
| 富文本协作 | diff / 撤销 / inline math 实时渲染 |
| 搜索升级 | tsvector 全文检索 / 标签 / 收藏 |
| 模板演化 | merge / branch / conflict / 版本对比 |
| 工具能力 | 工具 ACL / 在线热重载 |
| 领域工具 | AlphaFold / 多序列比对 / 本地 DB 自动同步 |
| Phase 2-6 推迟项 | while/for 嵌套 / break/continue / 自动重试 / 总结失真 / 选择性遗忘 / symlink / 群聊合签 |

**Phase 7 阶段门**：

| 阶段门 | 标准 |
|---|---|
| 设计门 | 本 spec 12 章用户确认 |
| ADR 门 | 4 个 ADR 用户评审 |
| 实施门 | plan 用户评审通过 |
| 测试门 | 45 新测试全部通过；336 老测试 0 回归 |
| 演示门 | 4 大模块全链路跑通（评论 / 搜索 / 公共模板审核 / fork）|

---

## 13. 演示场景

### 13.1 协作评论 fetch

```
用户："查询 doc_1 的评论"
  ↓ process_phase7
  ↓ CommentService.fetch_thread(doc_id="doc_1")
  ↓ 飞书 open_api: docx.comment.list?doc_id=doc_1
  ↓ 返回 comments
  ↓ IM reply："评论列表：\n- 张三: 这段不太清楚\n  ↳ 李四: @张三 已补充说明"

用户："查询 doc_1 的 block_a 评论"
  ↓ list_block_comments(doc_id, block_a)
  ↓ 仅返回锚定 block_a 的评论
```

### 13.2 模板搜索

```
GET /templates/search?q=blast&scope=public&limit=20
  ↓ PG trigram + ILIKE
  ↓ 命中：[std_report, blast_workflow]
  ↓ JSON: {"results": [{"id": "t_1", "name": "std_report", ...}, ...]}
```

### 13.3 公共模板审核

```
1. 用户 A 创建模板（scope='user'）→ /template-submit-public t_1
2. scope 变为 'public_pending'；audit 写 submit
3. admin 发 /admin/templates/review t_1 approve → scope='public'；audit 写 approve
4. 用户 B 搜索 scope=public → 看到 t_1
5. 用户 B /template-fork t_1 → 新私有模板（lineage=t_1）

或 admin reject：
  /admin/templates/review t_1 reject "内容不够完整"
  → scope 保持 'user'；audit 写 reject + reason
```

### 13.4 Fork 公共模板

```
1. 用户 B GET /templates/public → 看到 t_1（scope=public）
2. 用户 B /template-fork t_1 → 返回 t_2
3. t_2: owner_open_id=B, scope=user, lineage=t_1
4. 用户 B 编辑 t_2（独立）
5. 用户 B 可再次 /template-submit-public t_2 → 提交新审核
```