# 飞书科研闭环 Agent — Phase 10 设计稿

> 日期：2026-08-26
> 状态：Phase 10 设计稿（已确认 A,A,A,A,A 方案）
> 范围：依赖锁定 + 真 PG 验证（含迁移补齐）+ 本机质量门 + 飞书联调就绪包
> 性质：**清欠账轮**——不加业务功能，固化 Phase 1-9 功能基线
> 推迟：评论 webhook / 分布式锁 / zhparser / CI 远端流水线（Phase 11）
> 前置：[Phase 9](./2026-08-26-feishu-research-agent-phase9-design.md)（协作智能化，485 测试）
> 后置：Phase 10 实施计划 [`../plans/2026-08-26-feishu-research-agent-phase10.md`](../plans/2026-08-26-feishu-research-agent-phase10.md)

---

## 1. 范围与目标

### 1.1 Phase 10 主题

**真实环境联调与工程化**。9 个 Phase、485 个测试全部建在 mock + SQLite 地基上。Phase 1 测试总结与 Phase 8/9 记录的欠账已明确：双解释器依赖漂移（ulid/respx 曾缺失）、PG tsvector 分支未真库执行（ADR-0026 诚实记录）、**Alembic 迁移停留在 0001（Phase 2-9 的 12 张表从未建迁移）**、无质量门。本轮全部清掉。

### 1.2 范围（4 板块）

| 板块 | Phase 10 内容 | Phase 10 不做（推迟）|
|---|---|---|
| **依赖锁定** | 项目 `.venv`（Python 3.12 钉死）+ `requirements-lock.txt` 全量 freeze + README 环境章节重写 | uv/poetry 迁移 / 多平台矩阵 |
| **真 PG 验证** | docker-compose PG 16 + **补齐 0002 迁移（12 张表）** + `pytest -m pg` 分层测试（迁移/tsvector/GIN/search_v2 真库） | 生产部署 / 主从 / 连接池调优 |
| **本机质量门** | ruff（lint+format）+ pytest-cov 覆盖率 + pre-commit 钩子 + `scripts/check.ps1` 一键检查 | mypy 全量 / 远端 CI（无 git remote，Phase 11） |
| **飞书联调就绪** | `.env.example` 扩充 + `scripts/config_check.py` 启动前校验器（含单测）+ `docs/联调指南.md`（隧道/测试租户/checklist） | 索取真实凭证实测 / webhook 实测 |

### 1.3 关键约束

- **不阻塞于外部条件**：Docker daemon 已装（可启动）；飞书凭据不索取，就绪包做到"凭据到位即插即测"
- **现有 485 测试零回归**：PG 测试走独立 `-m pg` 标记，默认跑不触发（无 Docker 自动 skip 并说明）
- **迁移策略**：补 `0002` 覆盖 Phase 2-9 全部表；开发期 `create_all` 语义不变（测试夹具不受影响）
- **质量门从宽起步**：ruff 仅启默认规则子集（E/F + 少量），存量代码允许少量豁免标记，不搞大清洗

### 1.4 关键决策（用户已批：A,A,A,A,A）

| # | 决策点 | 选择 | ADR |
|---|---|---|---|
| 1 | Phase 10 范围 | A：联调工程化组合（4 板块，不加功能） | — |
| 2 | 依赖锁定 | A：.venv + requirements-lock.txt + Python 3.12 钉死 | 0028 |
| 3 | PG 验证方式 | A：docker-compose + `-m pg` 分层标记（无 Docker 自动跳过） | 0029 |
| 4 | 质量门 | A：本机质量门（ruff/cov/pre-commit/check 脚本），远端 CI 留 Phase 11 | — |
| 5 | 飞书策略 | A：就绪包（env 模板 + config_check 校验器 + 联调指南），不索凭据 | 0030 |

### 1.5 不做清单（明确边界）

- uv / poetry / conda 迁移
- 生产部署、K8s、连接池调优
- mypy 全量类型检查（存量未按类型标准写，Phase 11 评估增量引入）
- GitHub Actions / 远端 CI（无 git remote）
- 索取飞书测试租户凭据 / webhook 实测 / 真 LLM 调用
- 评论 webhook、分布式锁、zhparser（Phase 11 功能候选）
- 任何新业务功能

---

## 2. 总体架构

```
开发者本机
 ├─ .venv（Python 3.12.10 钉死）
 │    └─ requirements-lock.txt（pip freeze 全量，清华源可复现）
 ├─ docker-compose.yml（postgres:16-alpine，端口 5433 避让本机 PG）
 │    └─ pytest -m pg（PG_TEST_URL 指向容器）：
 │        0002 迁移全表验证 / tsvector 真分支 / GIN 索引 / search_v2 融合排序
 ├─ scripts/
 │    ├─ check.ps1          # 一键：ruff + pytest -q + pg 子集提示
 │    └─ config_check.py    # 启动前校验（env/配置/网络），退出码语义
 ├─ .pre-commit-config.yaml # ruff hooks
 └─ docs/联调指南.md        # 隧道方案 + 测试租户 checklist + 联调步骤
```

**无生产代码改动**（仅新增脚本/配置/迁移/测试与文档）；`pyproject.toml` 追加 ruff 配置与 pytest markers。

---

## 3. 依赖锁定（板块一）

### 3.1 目标

终结"双解释器漂移"：Phase 8 曾因本机 3.10/3.12 并存导致 ulid/respx 缺失。

### 3.2 交付物

1. **`.venv` 建于项目根**：Python 3.12.10（当前解释器），安装 `.[dev]` 全量
2. **`requirements-lock.txt`**：`pip freeze` 全量快照（含传递依赖精确版本），README 注明清华源复现命令
3. **README 环境章节重写**：版本要求 / venv 创建 / 锁文件安装 / 常见漂移问题
4. `.gitignore` 已忽略 `.venv/`（现状确认，无改动）

### 3.3 验收

- `.venv` 内 `python -m pytest tests/ -q` 全绿（锁环境回归 485）
- 锁文件被 git 追踪；README 指引可复现

---

## 4. 真 PG 验证（板块二，ADR-0029）

### 4.1 迁移补齐（硬前置）

**现状**：`migrations/versions/` 仅 `0001_init.py`（Phase 1 的 6 张表）；Phase 2-9 新增 12 张表（executions / approvals / plan_runtime_state / session_freezes / templates / template_versions / template_audit / comments / template_tags / template_favorites / comment_notify_log + sessions 的 Phase 3 字段）**无迁移**。

**交付**：`migrations/versions/0002_phase2_to_phase9_tables.py`——与 `Base.metadata` 逐表对齐（列/索引/唯一约束），upgrade 建 12 表 + sessions 增列；downgrade 反向。

### 4.2 docker-compose

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment: {POSTGRES_USER: agent, POSTGRES_PASSWORD: agent, POSTGRES_DB: agent}
    ports: ["5433:5432"]   # 避让本机可能存在的 5432
```

### 4.3 分层测试（`-m pg`，无 Docker 自动 skip）

| 测试 | 断言 |
|---|---|
| `test_pg_migrations.py::test_0002_creates_all_tables` | `alembic upgrade head` 后 17 张表齐 + 与 `Base.metadata` 表名集合相等 |
| `test_pg_migrations.py::test_downgrade_clean` | downgrade -2 后回到 0001 基线 |
| `test_pg_search_v2.py::test_tsvector_branch_real` | 真 PG 跑 `search_v2`：name/desc 命中分、ts_rank 生效 |
| `test_pg_search_v2.py::test_gin_index_usable` | 建 GIN 表达式索引后查询计划含 index scan（EXPLAIN 断言） |
| `test_pg_search_v2.py::test_fusion_ranking_pg` | Phase 9 E4 融合排序场景在 PG 复现 |
| `test_pg_search_v2.py::test_chinese_query_note` | 中文查询行为记录性断言（'simple' 配置逐词匹配；zhparser 留 Phase 11，spec 注明） |

跳过逻辑：session 级 fixture 探测 `PG_TEST_URL` 环境变量 + 连接失败 → `pytest.skip("PG 不可用，跳过 -m pg 层")`。

### 4.4 GIN 索引

迁移 0002 中为 templates 建：

```sql
CREATE INDEX ix_templates_fts ON templates
USING GIN (to_tsvector('simple', coalesce(name,'') || ' ' || coalesce(description,'')));
```

---

## 5. 本机质量门（板块三）

### 5.1 交付物

| 件 | 说明 |
|---|---|
| `pyproject.toml [tool.ruff]` | line-length 100；select E,F,I,W + `RUF` 少量；per-file ignore 测试目录放宽 |
| ruff 首轮清理 | 存量违规修复（预期：unused import / 长行少量；不做风格大改） |
| `.pre-commit-config.yaml` | ruff + ruff-format hooks（本地 hooks，无需网络仓库） |
| `pytest-cov` 接入 | `scripts/check.ps1` 出 coverage 摘要（不设硬阈值门，Phase 11 定基线） |
| `scripts/check.ps1` | `ruff check` → `ruff format --check` → `pytest -q` → 提示 `-m pg` 单独跑 |

### 5.2 约束

- ruff 修复**不得改变行为**：改后全量回归 485 + pg 层
- 存量文件大量风格问题时不搞大清洗：新增 per-file-ignores 或 `# noqa` 定点豁免

---

## 6. 飞书联调就绪包（板块四，ADR-0030）

### 6.1 交付物

1. **`.env.example` 扩充**：补 `PG_TEST_URL`（pg 测试）、`COMMENT_SYNC_INTERVAL_SEC`、各缺失项注释分组
2. **`scripts/config_check.py`**：启动前校验器，纯标准库实现：
   - Level 模型：`error`（缺必需凭据/配置文件）/ `warn`（可选未配）/ `info`
   - 校验项：必填 env（按 `.env.example` 分组）、config/*.yaml 可解析、DATABASE_URL 格式、LLM base_url 可达性（可选，超时 3s 容错）、Docker daemon 探测（可选）
   - 退出码：有 error → 2；仅 warn → 0（打印警告）
3. **`docs/联调指南.md`**：测试租户开通常识 checklist、内网穿透三方案对比（ngrok/cloudflared/飞书自建回调代理）、`.env` 填写顺序、`config_check` → `alembic upgrade` → `uvicorn` 启动序列、逐子系统冒烟（IM→bind-doc→评论闭环→模板市场）
4. **config_check 单测**（~8 个）：mock env/文件系统，测各级判定与退出码

---

## 7. 数据模型汇总（ORM 变更）

**无 ORM 变更**（17 表不变）。唯一 schema 变化在**迁移层**：0002 补建 12 张表 + sessions 增列 + GIN 索引，使 Alembic 与 `Base.metadata` 对齐。

---

## 8. API 与 IM 指令

**无新路由、无新指令**。全部交付物为：环境（venv/lock/compose）、迁移（0002）、测试（pg 层 + config_check）、工具（check.ps1 / config_check.py / pre-commit）、文档（README/联调指南）。

---

## 9. 测试策略

### 9.1 测试矩阵（计划 14：pg 层 6 + config_check 8）

| 层 | 文件 | 数 | 跑法 |
|---|---|---|---|
| pg | `tests/pg/test_pg_migrations.py` | 2 | `pytest -m pg`（需 docker compose up） |
| pg | `tests/pg/test_pg_search_v2.py` | 4 | 同上 |
| 单元 | `tests/unit/test_config_check.py` | 8 | 默认（mock，无网络） |

### 9.2 回归门

- 默认套件（SQLite）**485 全绿 0 回归**
- `-m pg` 层：Docker 可用时 6/6 绿；不可用时全部 skip（**不算失败**）
- ruff check 0 error

---

## 10. 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| Docker daemon 启动失败（Windows Desktop 未跑） | 中 | 就绪包文档写明启动步骤；pg 层自动 skip 不阻塞交付 |
| 0002 迁移与 ORM 有细微偏差（列类型/时区） | 中 | pg 层首个测试即"表名集合 + 关键列断言"；以 Base.metadata 为真源 |
| tsvector 'simple' 中文分词不符合预期 | 高（已知） | 记录性测试断言现状 + spec 明示 zhparser 留 Phase 11；不算失败 |
| ruff 首轮爆出大量存量违规 | 中 | 只修 E/F/I 自动安全项；per-file-ignores 兜底；不改行为 |
| freeze 锁文件含本机路径/egg-link | 低 | `-e .` 安装后 freeze 正常；README 注明复现步骤 |
| psycopg 驱动在 3.12 的 wheel | 低 | psycopg[binary] 已在依赖；venv 内验证 |

---

## 11. ADR 清单（Phase 10）

| ADR | 标题 | 决策 |
|---|---|---|
| 0028 | phase10-venv-lockfile | .venv + requirements-lock.txt 钉死 Python 3.12 |
| 0029 | phase10-pg-test-tier | docker-compose PG + `-m pg` 分层（自动 skip，不算失败） |
| 0030 | phase10-feishu-readiness-pack | 就绪包（校验器+指南），不索取凭据不阻塞 |

> 质量门为工具接入无架构取舍，不单独立 ADR（spec §5 即约定）。

---

## 12. 后续动作与阶段门

**Phase 11 候选**：

| 模块 | 候选 |
|---|---|
| 真实联调续 | 用户提供测试租户后实测 webhook/LLM/评论闭环（就绪包消耗） |
| CI | 建 git remote + GitHub Actions（ruff + pytest + pg service 容器） |
| 功能 | 评论 webhook / 分布式轮询锁 / zhparser / 卡片 approve-all |
| 质量 | mypy 增量引入 / coverage 硬阈值基线 |

**Phase 10 阶段门**：

| 阶段门 | 标准 |
|---|---|
| 设计门 | 本 spec 用户确认（✅ 2026-08-26 A,A,A,A,A） |
| ADR 门 | 3 个 ADR 用户可评审 |
| 实施门 | plan 用户确认后 inline 实施 |
| 测试门 | 默认 485 零回归；pg 层 6/6（Docker 可用时）；ruff 0 error |

---

## 13. 与 Phase 1-9 的接口契约（不变项）

- 全部 17 张 ORM 表结构与全部服务/路由/指令零改动
- `Base.metadata.create_all` 开发语义不变（测试夹具不受 0002 影响）
- Phase 7/8/9 全部路由与 IM 指令行为不变
- `search_v2` 代码不改动（真库验证其既有方言分支；若发现分支 bug 仅修分支内部）

---

## 14. 实施结果（交付后填写）

**状态：已完成（2026-08-27）**。测试：默认层 **493 passed**（485+8 新增 config_check 单测）+ pg 层 **6 passed**（docker compose 真库执行）；ruff 0 error；覆盖率 85%（3813 stmts）。

### 14.1 交付物清单

| 板块 | 文件 | 说明 |
|------|------|------|
| 依赖锁定 | `.venv` / `requirements-lock.txt`（43 行） | 3.12.10 + freeze；ruff/pytest-cov/pre-commit 入 dev 组并锁 |
| 迁移补齐 | `migrations/versions/0002_phase2_to_phase9.py` | 12 表 + sessions 三列 + templates GIN 表达式索引，downgrade 反向 |
| pg 层 | `docker-compose.yml`、`tests/pg/`（conftest + 2 模块） | postgres:16-alpine @5433；迁移 2 + 检索 4 测试 |
| 质量门 | `pyproject.toml [tool.ruff]`、`.pre-commit-config.yaml`、`scripts/check.ps1` | ruff check 硬门 + cov 摘要 + `-Pg` 真库门 |
| 就绪包 | `scripts/config_check.py` + 8 单测、`.env.example`、`docs/联调指南.md` | 纯标准库校验器（error/warn/info，exit 0/2） |

### 14.2 实施中修正（超出 plan 的发现）

1. **conftest `pytestmark` 不传播**：`-m pg` 标记必须写在各测试模块内，否则 6 项全 deselected。
2. **psycopg 方言前缀**：`postgresql://` 会让 SQLAlchemy 找 psycopg2（未装）；conftest/模板统一 `postgresql+psycopg://`，libpq 探测连接时剥离方言段。
3. **alembic.ini GBK 解码**：Windows 下 `Config("alembic.ini")` 按 locale（cp936）读中文注释崩；测试改程序化 cfg（仅 script_location），URL 由 env.py 经 `DATABASE_URL` 注入（conftest monkeypatch 齐飞书/LLM 九变量，migrations 不真用其值）。
4. **search_v2 PG 档位对齐**：裸 `ts_rank` 无法区分 name/desc 命中（单词元文档等分，排序随机）；改为 `case(name 命中=2.0, 全文命中=1.0) + ts_rank 微分`，与 SQLite 分支口径一致，WHERE 仍用与 GIN 索引一致的表达式（EXPLAIN 验证 bitmap index scan 命中）。
5. **ruff format 降级 informational**：存量 201 文件会被整体重排，违背"不改行为"约束；check.ps1 不以其为门，pre-commit 仅挂 ruff check（偏差记录，留后续专门轮）。
6. **EXPLAIN 断言细节**：`.scalar()` 只取计划首行（Bitmap Heap Scan）漏掉第二行 Index Scan；改 fetchall 拼接 + 小写比较；空表必 seq scan，故 `SET enable_seqscan=off` 强制暴露索引可用性。
7. **本机系统 Temp 受限**：pytest `tmp_path` 夹具 PermissionError；config_check 单测改 `tempfile.TemporaryDirectory(dir=ROOT)`。

### 14.3 联调就绪清单（见 `docs/联调指南.md`）

环境搭建 → 租户 checklist（6 项）→ 穿透三方案 → 启动序列（config_check → compose → alembic → uvicorn）→ 8 步冒烟 → 常见排障（含本轮全部新坑）。
