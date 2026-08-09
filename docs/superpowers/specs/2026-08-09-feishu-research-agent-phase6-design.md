# 飞书科研闭环 Agent — Phase 6 设计稿

> 日期：2026-08-09
> 状态：Phase 6 设计稿（待实施）
> 范围：富文本扩展（8 类增量）+ 模板版本回滚 + 群聊共享模板 + 本地 BLAST+ + 工具热加载
> 推迟：模板搜索 / 标签 / 导入导出 / Phase 2-5 推迟项（Phase 7）
> 前置：[Phase 1](./2026-08-08-feishu-research-agent-design.md) / [Phase 2](./2026-08-08-feishu-research-agent-phase2-design.md) / [Phase 3](./2026-08-09-feishu-research-agent-phase3-design.md) / [Phase 4 MVP](./2026-08-09-feishu-research-agent-phase4-mvp-design.md) / [Phase 5](./2026-08-09-feishu-research-agent-phase5-design.md)
> 后置：Phase 6 实施计划 [`../plans/2026-08-09-feishu-research-agent-phase6.md`](../plans/2026-08-09-feishu-research-agent-phase6.md)（待写）

---

## 1. 范围与目标

### 1.1 Phase 6 主题

在 Phase 5 富文本与模板基础上，扩展**富表达力**（14 类 block）、**模板治理**（版本 + 共享）、**本地科研能力**（BLAST+ 二进制）、**运维能力**（工具热加载）。

### 1.2 范围（4 子系统 + 1 工具扩展）

| 模块 | Phase 6 内容 | Phase 6 不做（推迟到 Phase 7）|
|---|---|---|
| **富文本扩展** | embed + divider + callout + equation + math + Mermaid + 视频 + 文件附件 | 协作评论 / diff / 撤销 |
| **模板版本** | template_versions 表 + 软版本 + rollback API | diff 视图 / branch / merge |
| **群聊共享** | scope（user / chat）+ chat_id 共享 | 公共模板 + 管理员审核 |
| **本地 BLAST+** | 检测本地二进制 + 离线模式 + 工具注册 | 本地数据库自动同步 |
| **工具热加载** | 管理员上传 .py → AST P0 拦截 → ToolRegistry 注入 | 在线动态重载（仅 restart）|

### 1.3 关键约束（继承 Phase 1-5）

- 富文本扩展**不破坏** Phase 5 6 类 block；纯增量
- 模板版本仅保留最近 N 个版本（默认 5）；硬上限 10
- 群聊共享 = `scope='chat'` + `chat_id`；同群成员可见可渲染；非成员 403
- 本地 BLAST+ 与 Phase 4 MVP web API 并存；按 `blast_local_path` 配置自动切换
- 工具热加载必须经 AST P0 拦截；不允许 `exec` / `eval` / `__import__` 等
- 群聊共享模板**不绕过** owner 校验；删除仍需 owner

### 1.4 关键决策（用户已批）

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| 1 | Phase 6 架构 | 方案 A（分模块并行）| 风险隔离 + 测试独立 |
| 2 | 富文本增量 | 8 类增量（embed + divider + callout + equation + math + Mermaid + 视频 + 文件附件）| 14 类总；解锁最强表达力 |
| 3 | 模板版本 | 软版本 + 回滚 API | 用户友好；保留最近 5 版 |
| 4 | 群聊共享 | scope='chat' + chat_id 字段 | 与 Phase 5 私有共存 |
| 5 | 工具扩展 | 仅本地 BLAST+ 二进制（Phase 7 再加 AlphaFold）| 范围聚焦 |
| 6 | 工具热加载 | 仅管理员 + AST P0 拦截 | 安全边界清晰 |
| 7 | 群聊模板管理员审核 | 不做（仅 owner 创建即可）| Phase 7 引入 |
| 8 | 模板版本上限 | 软上限 5，硬上限 10 | 防 DB 膨胀 |
| 9 | 本地 BLAST+ 默认 | 检测 `blast_local_path` 设置；未设置走 web API | 兼容 Phase 4 |
| 10 | 工具热加载 UI | 仅 CLI / API | 推到 Phase 7 |

### 1.5 不做清单（明确边界）

- 富文本协作评论 / @ 用户（Phase 7）
- 富文本 diff / 版本对比（Phase 7）
- 模板搜索 / 标签 / ES / trigram（Phase 7）
- 模板导入导出 / fork / merge（Phase 7）
- 公共模板 + 管理员审核流（Phase 7）
- AlphaFold / 多序列比对 / 本地数据库自动同步（Phase 7）
- 工具在线热重载（仅 restart 后生效；Phase 7）
- Phase 2/3/4/5 推迟项（while/for 嵌套 / break/continue / 自动重试 / 总结失真 / 选择性遗忘 / symlink / 群聊合签）（Phase 7）

---

## 2. 总体架构

```
FastAPI (Phase 5 不变)
 └─ Orchestrator.process_phase6(incoming)
   ├─ /template-list / /template-share / /template-rollback 指令（Phase 6 新）
   ├─ Planner（Phase 3）→ DAGPlan（不变）
   ├─ Scheduler → 展开 sub-Plan（Phase 5 不变）
   ├─ LocalExecutor → ToolHandler.execute()
   │  ├─ ToolRegistry（Phase 6 +hot_loader）
   │  │  ├─ Phase 2 L0/L1/L2（不变）
   │  │  ├─ Phase 4 BLAST search（web API）不变
   │  │  ├─ Phase 6 BLAST search（local）NEW：根据 blast_local_path 自动选择
   │  │  └─ Phase 6 管理员上传的工具（NEW）
   │  └─ blocks 字段（Phase 5 +14 类增量）
   ├─ TemplateEngine.render_blocks_to_text（Phase 5 +14 类映射）
   └─ DocAdapter.render_blocks（Phase 5 +14 类飞书 API）
```

**新增模块**：
- `orchestrator/blocks/`（Phase 5 升级）+8 类 block
- `orchestrator/templates/version_service.py`：版本管理
- `orchestrator/templates/share_service.py`：群聊共享
- `orchestrator/tools/bio/blast_local.py`：本地 BLAST+
- `orchestrator/tools/hot_loader.py`：管理员上传 + AST check

**升级模块**：
- `templates` 表：+scope / chat_id 字段
- `template_versions` 表（新增）
- `ToolRegistry`：动态注入 hot-loaded 工具
- `Orchestrator`：process_phase6 + 3 个新 IM 指令

---

## 3. 富文本扩展（8 类增量）

### 3.1 目标

Phase 5 6 类 block 增至 14 类。新增：embed + divider + callout + equation + math + Mermaid + 视频 + 文件附件。

### 3.2 新增 Block 类型（8 类）

| 类型 | 必填字段 | 可选字段 | 飞书 doc 映射 |
|---|---|---|---|
| **embed** | url | title, description | embed block（iframe）|
| **divider** | - | - | divider block |
| **callout** | emoji, text, color | - | callout block |
| **equation** | latex | - | equation block（KaTeX 渲染）|
| **math** | latex, display_mode | - | equation block（与 equation 合并字段）|
| **mermaid** | code | theme | code block + mermaid 注释 |
| **video** | url | poster_url, duration | video block |
| **file** | file_token, name | size | file block |

### 3.3 Pydantic schema（增量）

```python
# orchestrator/blocks/schemas.py （Phase 6 增量）

class EmbedBlock(BaseModel):
    type: Literal["embed"] = "embed"
    url: str = Field(pattern=r"^https?://")
    title: str = ""
    description: str = ""


class DividerBlock(BaseModel):
    type: Literal["divider"] = "divider"


class CalloutBlock(BaseModel):
    type: Literal["callout"] = "callout"
    emoji: str  # 🟢 ⚠️ 📌
    text: str
    color: str = "blue"  # blue / red / yellow / green


class EquationBlock(BaseModel):
    type: Literal["equation"] = "equation"
    latex: str


class MathBlock(BaseModel):
    type: Literal["math"] = "math"
    latex: str
    display_mode: bool = True  # block vs inline


class MermaidBlock(BaseModel):
    type: Literal["mermaid"] = "mermaid"
    code: str
    theme: str = "default"


class VideoBlock(BaseModel):
    type: Literal["video"] = "video"
    url: str = Field(pattern=r"^https?://")
    poster_url: str | None = None
    duration: int | None = None  # 秒


class FileBlock(BaseModel):
    type: Literal["file"] = "file"
    file_token: str
    name: str
    size: int = 0  # bytes


AnyBlock_v6 = Union[
    HeadingBlock, TextBlock, CodeBlock, QuoteBlock, QuoteContainerBlock,
    TableBlock, ListBlock, ImageBlock,
    EmbedBlock, DividerBlock, CalloutBlock, EquationBlock,
    MathBlock, MermaidBlock, VideoBlock, FileBlock,
]
```

### 3.4 飞书 doc API 映射

| Block | 飞书 API block_type |
|---|---|
| embed | embed |
| divider | divider |
| callout | callout |
| equation | equation（KaTeX）|
| math | equation（与 equation 同字段）|
| mermaid | code_block + 注释（飞书不支持原生 mermaid；退化）|
| video | video |
| file | file |

**Mermaid 退化**：飞书 doc API 不支持 mermaid 渲染。Phase 6 退化方案：渲染为 code block（language="mermaid"）+ 注释 "需在支持 mermaid 的环境查看"。

### 3.5 DocAdapter 升级

在 Phase 5 `_to_feishu_payload()` 中追加 8 类分支。限流策略不变（3 req/s）。

### 3.6 TemplateEngine 升级

`render_blocks_to_text()` 追加 8 类文本化分支：
- embed → `[embed: title](url)`
- divider → `---`
- callout → `⚠️ text`
- equation / math → `$$latex$$`
- mermaid → `\`\`\`mermaid\ncode\n\`\`\``
- video → `[video: url]`
- file → `[file: name (size bytes)]`

---

## 4. 模板版本（template_versions + 回滚）

### 4.1 目标

用户每次更新模板保存到 versions 表；保留最近 N 版；提供 rollback API。

### 4.2 数据模型

```sql
CREATE TABLE template_versions (
    version_id      TEXT PRIMARY KEY,
    template_id     TEXT NOT NULL REFERENCES templates(template_id),
    version_number  INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL,
    blocks_json     TEXT,
    steps_json      TEXT,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    created_by      TEXT NOT NULL  -- 触发版本的用户 open_id
);
CREATE INDEX ix_template_versions_template_id
    ON template_versions (template_id, version_number DESC);
```

### 4.3 版本生成策略

- **创建模板** → 写 v1
- **更新模板**（如果 owner 显式声明 `update_with_version=True`）→ 写 v(N+1)
- 否则：直接覆盖 templates 表（与 Phase 5 兼容）

**简化版**：Phase 6 每次 update 都生成新版本（无论是否声明）。

### 4.4 版本上限

- 软上限 5（超过不报错，但可在 `/templates/{id}/versions` 看到提醒）
- 硬上限 10（超过自动删除最旧）

### 4.5 rollback API

```python
POST /templates/{template_id}/rollback
body: {"version_number": 3, "caller_open_id": "ou_1"}
→ 校验 caller 是 owner
→ 取 template_versions v3 内容
→ 写入 templates 表
→ 写新版本 v(N+1)（内容 = v3）
```

### 4.6 列出版本

```python
GET /templates/{template_id}/versions
→ 返回 template_versions 列表（按 version_number DESC）
```

### 4.7 VersionService

```python
class VersionService:
    def __init__(self, version_repo: TemplateVersionRepo,
                 template_repo: TemplateRepo):
        self.version_repo = version_repo
        self.template_repo = template_repo
    
    def on_template_upsert(self, *, template_id: str, ...):
        """Phase 6: 每次 upsert 写新版本。"""
        current_count = self.version_repo.count(template_id)
        if current_count >= 10:
            self.version_repo.delete_oldest(template_id, keep=10)
        new_version = current_count + 1
        self.version_repo.insert(
            version_id=new_ulid(),
            template_id=template_id,
            version_number=new_version,
            ...
        )
    
    def list_versions(self, template_id: str) -> list[TemplateVersionRow]:
        return self.version_repo.list_by_template(template_id)
    
    def rollback(self, *, template_id: str, version_number: int,
                 caller_open_id: str) -> None:
        tpl = self.template_repo.get(template_id)
        if tpl.owner_open_id != caller_open_id:
            raise PermissionError("not owner")
        version = self.version_repo.get_by_version(
            template_id, version_number
        )
        # 覆盖 templates 表 + 写新版本
        self.template_repo.upsert(
            template_id=template_id, ...version 内容...
        )
        self.on_template_upsert(...)  # 触发版本记录
```

### 4.8 权限

rollback 仍需 owner 校验；非 owner 403。

---

## 5. 群聊共享模板

### 5.1 目标

模板可设 `scope='chat'`，同群成员可见可渲染。

### 5.2 数据模型变更

```sql
-- templates 表新增字段
ALTER TABLE templates ADD COLUMN scope TEXT NOT NULL DEFAULT 'user';
-- 'user' | 'chat'
ALTER TABLE templates ADD COLUMN chat_id TEXT;
CREATE INDEX ix_templates_chat_id ON templates (chat_id) WHERE scope = 'chat';
```

### 5.3 共享流程

```
1. 用户 A 在群聊 chat_1 中创建模板（scope='user'）
2. 用户 A 发 /template-share <id>：
   - 把 templates 表的 scope='chat', chat_id=<current_chat_id>
   - 同群所有用户可见
3. 同群用户 B：
   - GET /templates/?owner_open_id=<B>：仍仅列出 B 私有
   - GET /templates/?chat_id=chat_1：列出群共享
   - 渲染：ts.render_block(template_id=...) — 需校验 B 在 chat_1 中
```

### 5.4 IM 指令

```
/template-share <template_id>
/template-list-chat
```

### 5.5 ShareService

```python
class ShareService:
    def __init__(self, template_repo, share_audit_repo=None):
        self.template_repo = template_repo
    
    def share_to_chat(self, *, template_id: str, chat_id: str,
                      caller_open_id: str) -> None:
        tpl = self.template_repo.get(template_id)
        if tpl is None:
            raise ValueError("not found")
        if tpl.owner_open_id != caller_open_id:
            raise PermissionError("not owner")
        self.template_repo.upsert(
            template_id=template_id,
            scope="chat",
            chat_id=chat_id,
        )
    
    def list_for_chat(self, chat_id: str) -> list[TemplateRow]:
        return self.template_repo.list_by_chat(chat_id)
    
    def can_access(self, *, template_id: str, caller_open_id: str,
                   chat_id: str | None) -> bool:
        tpl = self.template_repo.get(template_id)
        if tpl is None or tpl.archived_at:
            return False
        if tpl.scope == "user":
            return tpl.owner_open_id == caller_open_id
        if tpl.scope == "chat":
            return tpl.chat_id == chat_id
        return False
```

### 5.6 群聊成员校验

**简化版**：Phase 6 不做实时群成员校验（仅校验 `chat_id` 匹配）。生产部署需结合飞书 API 校验 sender 是否在群中（Phase 7）。

### 5.7 渲染与删除权限

- 渲染：群内所有成员可渲染（`can_access` 返回 True）
- 删除：仅 owner 可删（与 Phase 5 一致）

---

## 6. 本地 BLAST+

### 6.1 目标

用户安装本地 `blast+` 二进制 + 数据库后，Agent 自动切换到本地模式（无需网络）。

### 6.2 配置

```python
# config/settings.py（Phase 6 增量）
blast_local_path: str = ""  # e.g. "/usr/local/bin/blastp"
blast_local_db_path: str = ""  # e.g. "/data/blastdb/nr"
blast_local_max_hits: int = 5
blast_local_timeout_sec: int = 60  # 本地可能慢
blast_mode: Literal["auto", "web", "local"] = "auto"
```

### 6.3 模式切换逻辑

```python
class BlastNCBITool (Phase 4):
    """Phase 6: 增加本地模式。"""
    def __init__(self, *, mode="auto", local_binary_path="", ...):
        self.mode = mode
        self.local_binary_path = local_binary_path
        ...
    
    def handle(self, *, query, database, max_hits):
        actual_mode = self._resolve_mode(database)
        if actual_mode == "local":
            return self._local_blast(query, database, max_hits)
        return self._web_blast(query, database, max_hits)
    
    def _resolve_mode(self, database):
        if self.mode == "local":
            return "local"
        if self.mode == "web":
            return "web"
        # auto: local 优先
        if self.local_binary_path and os.path.exists(self.local_binary_path):
            if self._local_db_exists(database):
                return "local"
        return "web"
```

### 6.4 本地 BLAST 实现

```python
import subprocess

def _local_blast(self, query, database, max_hits):
    """调 blast+ 二进制，返回 hits。"""
    cmd = [
        self.local_binary_path,
        "-query", query,
        "-db", os.path.join(self.local_db_path, database),
        "-outfmt", "15",  # JSON
        "-max_target_seqs", str(max_hits),
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=self.local_timeout_sec,
    )
    if proc.returncode != 0:
        return {"error_code": "BLAST_LOCAL_ERROR",
                "error_message": proc.stderr}
    return self._parse_blast_json(proc.stdout)
```

### 6.5 工具注册升级

`register_l3_bio` 根据 `blast_mode` 自动注册本地或 web 工具（或两者都注册，按 priority 优先选择）。

**Phase 6 简化**：仅注册一个 `blast_search` 工具，模式由 `BlastNCBITool` 内部决定。

### 6.6 数据库检测

`_local_db_exists(database)` 检查 `blast_local_db_path/database.{psq,nsq,phr,nhr}` 等 BLAST DB 文件存在。

---

## 7. 工具热加载

### 7.1 目标

管理员通过 CLI / API 上传工具实现（.py 文件），经 AST P0 拦截后注册到 ToolRegistry。

### 7.2 上传流程

```
1. 管理员调用 POST /admin/tools/upload：
   body: {
     "name": "my_tool",
     "description": "...",
     "code": "def handle(**kwargs): ...",
     "parameters": {...},
     "risk_level": "L0_read",
   }

2. HotLoader.execute():
   - 把 code 写临时文件
   - 调 ASTGuard.check(code) — 复用 Phase 3 AST P0 拦截
   - 如果 blocked → 抛 ToolBlockedError
   - 否则：
     - importlib 加载临时文件（限定命名空间）
     - 提取 handle 函数
     - 注册到 ToolRegistry
     - 写 audit: action="hot_load_tool"

3. 返回 tool_name + version
```

### 7.3 ASTGuard 升级

复用 Phase 3 的 `check()`，但对上传代码**更严格**：
- 禁止 `import os` / `import subprocess`（保留 `import requests` 但 P1 提示）
- 禁止 `eval` / `exec` / `__import__`
- 禁止文件写：`open(..., "w")`

**Phase 6 简化**：复用 Phase 3 ASTGuard，不增加新规则。

### 7.4 HotLoader

```python
class HotLoader:
    def __init__(self, tool_registry, audit_repo, ast_guard,
                 temp_dir="/tmp:hot_tools"):
        self.tool_registry = tool_registry
        self.audit_repo = audit_repo
        self.ast_guard = ast_guard
        self.temp_dir = temp_dir
    
    def upload(self, *, name, code, parameters, risk_level,
               actor_open_id) -> str:
        # AST P0 拦截
        result = self.ast_guard.check(code)
        if result.blocked:
            raise ToolBlockedError(result.notices)
        # 写临时文件
        os.makedirs(self.temp_dir, exist_ok=True)
        module_id = new_ulid()
        path = os.path.join(self.temp_dir, f"{module_id}.py")
        with open(path, "w") as f:
            f.write(code)
        # importlib 加载
        spec = importlib.util.spec_from_file_location(module_id, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        handle = getattr(module, "handle", None)
        if handle is None:
            raise ValueError("tool must define handle(**kwargs)")
        # 注册
        self.tool_registry.register(ToolSpec(
            name=name, description="hot-loaded",
            parameters=parameters, risk_level=risk_level,
            handler=handle,
        ))
        # 审计
        self.audit_repo.write(
            action="hot_load_tool", actor_type="admin",
            actor_id=actor_open_id, target_type="tool",
            target_id=name, detail={"module_id": module_id},
        )
        return name
```

### 7.5 审计 + 撤销

- 所有 hot_load 写 audit（Phase 1 AuditRepo）
- 撤销：`DELETE /admin/tools/{name}`（仅 admin 可调）
  - 调用 `tool_registry.unregister(name)`
  - 写 audit: action="hot_unload_tool"

### 7.6 安全边界

- 仅 admin 可调（Phase 7 引入 ACL；Phase 6 仅靠 secret 共享）
- code 大小上限 100KB
- 不允许覆盖已有 tool（除非显式声明 `replace=True`）

---

## 8. PostgreSQL schema 增量

### 8.1 新增表

```sql
-- 模板版本（§4）
CREATE TABLE template_versions (
    version_id      TEXT PRIMARY KEY,
    template_id     TEXT NOT NULL,
    version_number  INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL,
    blocks_json     TEXT,
    steps_json      TEXT,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    created_by      TEXT NOT NULL
);
CREATE INDEX ix_template_versions_template_id
    ON template_versions (template_id, version_number DESC);
```

### 8.2 templates 表字段扩展

```sql
ALTER TABLE templates ADD COLUMN scope TEXT NOT NULL DEFAULT 'user';
ALTER TABLE templates ADD COLUMN chat_id TEXT;
CREATE INDEX ix_templates_chat_id ON templates (chat_id) WHERE scope = 'chat';
```

### 8.3 迁移

新增 `migrations/versions/0006_phase6_*.py`。

---

## 9. 测试策略

### 9.1 测试矩阵

| 子系统 | 单元 | 集成 | 端到端 |
|---|---|---|---|
| 富文本扩展（8 类 schema + 序列化）| 12 | - | - |
| 富文本飞书 doc 渲染 | - | 8 | - |
| TemplateVersionRepo CRUD | 3 | - | - |
| VersionService（创建 / 列出 / 回滚）| 4 | - | - |
| 版本上限（10 上限）| 2 | - | - |
| ShareService（share / list / can_access）| 5 | - | - |
| BlastLocal（解析 / 错误 / 模式切换）| 5 | - | - |
| HotLoader（AST 拦截 / 注册 / 审计）| 5 | - | - |
| FastAPI 新路由（/versions / /share / /admin/tools）| - | 8 | - |
| E1-E8 端到端 | - | - | 8 |
| **合计** | **36** | **16** | **8** |

### 9.2 E1-E8 端到端场景

| # | 场景 | 验证点 |
|---|---|---|
| E1 | 富文本 14 类 block → 飞书 doc | embed + divider + callout + equation + math + Mermaid + 视频 + 文件 |
| E2 | 模板版本：3 次更新 → 版本列表 v1-v3 → rollback v2 | 内容正确恢复 |
| E3 | 版本上限 10：写入 11 个 → 最旧被删 | DB 容量控制 |
| E4 | 群聊共享：用户 A 创建 → /template-share → 用户 B 在同群可渲染 | scope='chat' + chat_id 校验 |
| E5 | 本地 BLAST：mock blastp 二进制 → 解析 JSON 输出 | hits 提取 |
| E6 | 模式自动切换：本地二进制存在 → 走 local；不存在 → web | mode='auto' 决策 |
| E7 | 热加载：管理员上传 → AST P0 拦截 → 注册 | ToolBlockedError + 成功路径 |
| E8 | 热加载：上传含 `eval` → 拦截 + audit | ToolBlockedError |

### 9.3 mock 策略

- **本地 BLAST**：mock `subprocess.run`
- **热加载**：临时目录隔离；测试结束清理
- **飞书 doc API**：respx（继承 Phase 5）

### 9.4 覆盖率目标

| 模块 | 目标 |
|---|---|
| 新增 8 类 Block schema | ≥ 90% |
| VersionService | ≥ 85% |
| ShareService | ≥ 85% |
| BlastLocal | ≥ 80% |
| HotLoader | ≥ 85% |
| **Phase 6 新增整体** | ≥ 80% |

### 9.5 回归保证

- Phase 1-5 全部 278 测试**必须**继续通过
- Phase 5 6 类 block API 不破坏（仅增量）
- Phase 4 BLAST web API 不破坏（mode='web' 强制走 web）

---

## 10. Phase 6 不做（明确边界）

| 项 | 推迟到 | 原因 |
|---|---|---|
| 富文本协作评论 / @ 用户 | Phase 7 | MVP 单向写入 |
| 富文本 diff / 版本对比 | Phase 7 | MVP 不做版本对比 |
| 富文本内联 math / Mermaid 实时渲染 | Phase 7 | 飞书不支持；退化 |
| 模板搜索 / 标签 / ES / trigram | Phase 7 | MVP 仅按 owner / chat 列表 |
| 模板导入导出（JSON / YAML）| Phase 7 | MVP 不做 |
| 模板 fork / merge / branch | Phase 7 | MVP 不做 |
| 公共模板 + 管理员审核流 | Phase 7 | MVP 群聊共享足够 |
| 群聊成员实时校验（飞书 API）| Phase 7 | MVP 仅 chat_id 匹配 |
| AlphaFold / 多序列比对 | Phase 7 | MVP 仅 BLAST+ |
| 本地数据库自动同步（NCBI update_blastdb.pl）| Phase 7 | 用户手动安装 |
| 工具在线热重载（仅 restart 后生效）| Phase 7 | MVP restart 后生效 |
| 工具热加载 ACL | Phase 7 | MVP 仅 secret 共享 |
| Phase 2-5 推迟项 | Phase 7 | 全推迟 |

---

## 11. 风险与决策

### 11.1 已识别风险

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 富文本 8 类增量破坏 Phase 5 序列化 | 严格只增不删；保留 AnyBlock Union |
| R2 | 模板版本 DB 膨胀 | 软上限 5 + 硬上限 10 |
| R3 | 群聊共享权限漏洞（未校验群成员）| Phase 6 简化：仅校验 chat_id；Phase 7 加飞书 API |
| R4 | 本地 BLAST subprocess 注入 | query / database 走白名单；不使用 shell=True |
| R5 | 热加载恶意代码（绕过 AST）| AST P0 拦截 + admin secret + 100KB 上限 |
| R6 | 模板版本时间线 race condition | on_template_upsert 用事务（Phase 6 简化：单线程 ORM 足够）|
| R7 | 富文本 mermaid 飞书不支持 | 退化为 code block + 注释 |
| R8 | 模板版本覆盖模板自身 rollback | rollback 写新版本（不覆盖历史）|

### 11.2 ADR（决策记录）待写

| # | ADR 主题 |
|---|---|
| 0010 | 富文本 14 类 vs 仅扩展 4 类 |
| 0011 | 模板版本软上限（5/10）|
| 0012 | 群聊共享 vs 公共模板 |
| 0013 | 本地 BLAST+ vs 仅 web API |
| 0014 | 工具热加载 vs 仅 restart |

---

## 12. ADR 清单（Phase 6）

待 Phase 6 实施前写 5 个 ADR：

1. **0010 phase6-block-extension**：14 类总（Phase 5 6 + Phase 6 8）
2. **0011 phase6-template-versioning**：软上限 5，硬上限 10
3. **0012 phase6-template-scope**：scope（user / chat），不做 public
4. **0013 phase6-blast-local**：本地 blast+ 二进制优先；web 兜底
5. **0014 phase6-hot-loader**：管理员上传 + AST P0 拦截 + audit

---

## 13. 后续动作

**Phase 6 实施前**：

1. ✅ **写完本 spec**：13 章全部展开
2. ⏭️ **写 Phase 6 plan**（writing-plans skill）
3. ⏭️ **写 5 个 ADR**（决策记录）
4. ⏭️ **用户评审 plan + ADR**
5. ⏭️ **Inline 实施**（沿用 Phase 3-5 节奏）

**Phase 7 候选范围**：

| 模块 | Phase 7 候选 |
|---|---|
| 富文本协作 | 评论 / @ / diff / 撤销 / 内联 math 渲染 |
| 模板市场升级 | 搜索 / 标签 / 导入导出 / fork / merge / 公共模板 + 审核 |
| 群聊权限 | 实时校验（飞书 API）/ 公共模板 ACL |
| 工具热加载 | 在线动态重载 / ACL |
| 领域工具 | AlphaFold / 多序列比对 / 本地 DB 自动同步 |
| Phase 2-5 推迟项 | while/for 嵌套 / break/continue / 自动重试 / 总结失真 / 选择性遗忘 / symlink / 群聊合签 |

**Phase 6 阶段门**：

| 阶段门 | 标准 |
|---|---|
| 设计门 | 本 spec 13 章用户确认 |
| ADR 门 | 5 个 ADR 用户评审 |
| 实施门 | plan 用户评审通过 |
| 测试门 | 60 新测试全部通过；278 老测试 0 回归 |
| 演示门 | 5 大模块全链路跑通（富文本 14 类 / 版本回滚 / 群聊共享 / 本地 BLAST / 热加载）|

---

## 14. 演示场景

### 14.1 富文本 14 类

```python
blocks = [
    HeadingBlock(level=2, text="实验报告"),
    DividerBlock(),
    CalloutBlock(emoji="📌", text="Important note"),
    TableBlock(headers=["Hit", "E-value"], rows=[["111", "1e-50"]]),
    EmbedBlock(url="https://example.com", title="Reference"),
    EquationBlock(latex="E = mc^2"),
    VideoBlock(url="https://video.example.com/exp1.mp4"),
]
```

### 14.2 模板版本 + 回滚

```
1. POST /templates/block v1: std_report
2. PUT /templates/v1 → v2 内容
3. PUT /templates/v2 → v3 内容
4. GET /templates/v1/versions → 列出 v1, v2, v3
5. POST /templates/v1/rollback version=2 → 模板回到 v2 内容
```

### 14.3 群聊共享

```
1. 用户 A 在 chat_1 创建 template
2. /template-share <id>（群内指令）
3. 用户 B（同群）GET /templates/?chat_id=chat_1 → 看到该模板
4. 用户 B 渲染模板 → success
5. 用户 C（其他群）GET 同一模板 → 403
```

### 14.4 本地 BLAST+

```
配置：
  blast_local_path: "/usr/local/bin/blastp"
  blast_local_db_path: "/data/blastdb"
  blast_mode: "auto"

用户发："blast BRCA1"
  ↓ mode='auto'：检测到本地二进制 → 走 local
  ↓ subprocess.run(['blastp', ...])
  ↓ 返回 hits（无网络调用）
```

### 14.5 工具热加载

```
1. 管理员 POST /admin/tools/upload
   body: {
     "name": "reverse_complement",
     "code": "def handle(seq: str) -> dict: return {'rc': seq[::-1]}",
     "parameters": {...},
     "risk_level": "L0_read",
   }
2. ASTGuard.check() → 通过（无 exec / eval）
3. ToolRegistry.register() → 立即可用
4. 用户发 "reverse complement ATCG" → ToolHandler.execute() → 成功
5. audit: hot_load_tool 写库

管理员发恶意代码（含 eval）：
  → ASTGuard.check() → blocked
  → ToolBlockedError → 拒绝
  → audit: blocked_tool_upload
```