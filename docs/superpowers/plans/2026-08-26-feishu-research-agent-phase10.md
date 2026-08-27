# Phase 10 实施计划 — 联调与工程化（清欠账轮）

> 日期：2026-08-26
> Spec: [`../specs/2026-08-26-feishu-research-agent-phase10-design.md`](../specs/2026-08-26-feishu-research-agent-phase10-design.md)
> ADRs: 0028-0030
> 模式：Inline 实施
> 基线：Phase 9 提交 8ee58d3，485 测试全绿（SQLite 层）

---

## 任务总览

| # | 任务 | 交付物 | 新测试 | 依赖 |
|---|---|---|---|---|
| T1 | venv + 依赖锁定 + README | `.venv` / `requirements-lock.txt` / README 重写 | — | — |
| T2 | 迁移补齐 0002 | `migrations/versions/0002_phase2_to_phase9_tables.py` | — | — |
| T3 | docker-compose + pg 层测试 | `docker-compose.yml` / `tests/pg/` 2 文件 | 6（pg 标记） | T2 |
| T4 | ruff 接入 + 首轮清理 | pyproject `[tool.ruff]` + 存量修复 | — | T1 |
| T5 | pre-commit + check.ps1 + cov | `.pre-commit-config.yaml` / `scripts/check.ps1` | — | T4 |
| T6 | config_check 校验器 + 单测 | `scripts/config_check.py` | 8 | — |
| T7 | .env.example 扩充 + 联调指南 | env 模板 / `docs/联调指南.md` | — | T6 |
| T8 | 全量回归 + pg 层执行 + spec §14 + 测试总结 + commit | 文档 | — | 全部 |

**计划新增测试：14**（pg 6 + config_check 8）→ 默认层预期 **493**；pg 层 `-m pg` 单跑。

---

## T1: venv + 锁定

```
python -m venv .venv                 # 3.12.10
.venv\Scripts\pip install -e ".[dev]" -i 清华源
.venv\Scripts\pip freeze > requirements-lock.txt
.venv 内跑 pytest 确认 485 绿
```

README 重写：项目简介（Phase 1-10 概览）/ 环境（3.12 钉死 + venv + 锁文件复现）/ 启动 / 测试（默认层 + pg 层）/ 常见漂移排障。

## T2: 迁移 0002

以 `Base.metadata` 为真源，upgrade 建 12 表（executions/approvals/plan_runtime_state/session_freezes/templates/template_versions/template_audit/comments/template_tags/template_favorites/comment_notify_log）+ sessions 增列（archived_at/origin_session_id/token_count）+ templates GIN 表达式索引；downgrade 反向。生成方式：`alembic revision --autogenerate`（对 PG）后人工核对，或手写（保确定性）。

## T3: compose + pg 测试

`docker-compose.yml`（postgres:16-alpine，5433:5432）。

`tests/pg/conftest.py`：session fixture 读 `PG_TEST_URL`（默认 `postgresql://agent:agent@localhost:5433/agent`），连接失败/未设 → `pytest.skip("PG 不可用（docker compose up -d postgres 后重试）")`；每 session 独立临时 schema 或 drop_all/create_all 隔离。

- `test_pg_migrations.py`（2）：0002 后 17 表齐且与 Base.metadata 表名集合相等；downgrade -2 回 0001 基线
- `test_pg_search_v2.py`（4）：tsvector 真分支命中分 / GIN 索引 EXPLAIN 使用 / 融合排序（E4 复现）/ 中文 simple 现状记录

`pyproject.toml [tool.pytest.ini_options]`：`markers = ["pg: 需要 docker compose 的 PG 真库"]`；`addopts` 不含 `-m`（默认全跑，pg 层靠自身 skip；文档给 `-m "not pg"` 快捷指令）。

## T4: ruff

`[tool.ruff]` line-length=100，select `["E","F","I","W"]`，`[tool.ruff.lint.per-file-ignores]` 测试目录放宽 E501 等。首轮 `ruff check --fix` + 人工核对 diff（**不改行为**），全量回归。

## T5: pre-commit + check.ps1

`.pre-commit-config.yaml`（本地 hooks：ruff、ruff-format）。`scripts/check.ps1`：venv 激活提示 → ruff check → ruff format --check → pytest -q（含 cov 摘要）→ 提示 `-m pg` 层。

## T6: config_check

`scripts/config_check.py` 纯标准库：`--env-file`（默认 .env）/ 必填组（FEISHU_APP_ID/SECRET/WEBHOOK_SECRET、LLM_*6、DATABASE_URL）/ 可选组（PG_TEST_URL 等）/ yaml 解析 / DATABASE_URL 前缀 / LLM base_url 可达性（--probe 开关，3s 超时）/ Docker 探测（--docker）。返回结构 `{"errors": [], "warnings": [], "infos": []}` + exit code。

单测（8）：mock env dict / 临时 yaml；覆盖：全缺 error、全齐 0 error、yaml 坏 warn、DATABASE_URL 非 pg 前缀 error、可达性探测超时 warn、exit code 2/0、可选组缺失只 warn、--probe 关闭不联网。

## T7: env 模板 + 指南

`.env.example` 补 PG_TEST_URL / COMMENT_SYNC_INTERVAL_SEC / 注释分组（必填/可选/测试）。`docs/联调指南.md`：租户 checklist、穿透三方案、启动序列（config_check → alembic upgrade → uvicorn）、逐子系统冒烟清单。

## T8: 收尾

1. `.venv` 内：`pytest tests/ -q`（默认层，pg skip）→ 493 绿
2. Docker 可用则：`docker compose up -d postgres` → `pytest -m pg -q` → 6 绿（daemon 起不来则记录 skip 并在测试总结注明）
3. spec §14 / 测试总结 / 4 个 commit（按板块分）

---

## 执行顺序

```
T1 → T2 → T4 → T3 → T5 → T6 → T7 → T8
（T4 在 T3 前保证 ruff 清理不与 pg 测试新写冲突）
```

风险预案：Docker daemon 起不来 → pg 层交付代码 + skip 记录（ADR-0029 允许）；autogenerate 不可用 → 手写 0002；ruff 修复引回归 → 只保留 --fix 安全项。
