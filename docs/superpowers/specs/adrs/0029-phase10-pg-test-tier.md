# ADR-0029: PG 验证采用 docker-compose + `-m pg` 分层测试（不可用自动跳过）

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-26）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 10 |
| 相关 spec | §4 真 PG 验证 |
| 前置欠账 | ADR-0026（PG tsvector 分支未经真库执行的诚实记录）|

---

## 背景

Phase 9 `search_v2` 的 PG 分支（tsvector/ts_rank/GIN）只在 SQLite 降级路径全量测试过；且 Alembic 迁移停留在 0001，Phase 2-9 的 12 张表无迁移。需真库验证，但本机 Docker daemon 未必常开。

---

## 选项

### A. docker-compose PG + `-m pg` 标记分层（已选 ✅）

compose 起 `postgres:16-alpine`（端口 **5433** 避让本机 5432）；PG 专属测试打 `pg` 标记，默认 `pytest` 不跑；session 级 fixture 探测 `PG_TEST_URL` + 连接失败 → `pytest.skip`。

- **优点**：干净隔离（PG 状态不污染默认套件）；Docker 不可用时交付不被阻塞（skip 不是 fail）；迁移/索引/方言分支一次性真验证
- **缺点**：依赖 Docker Desktop 手动启动（Windows）；pg 层不会进日常默认回归

### B. 本机安装 PostgreSQL 服务

- **优点**：常驻可用
- **缺点**：污染系统环境；版本管理/卸载麻烦；端口冲突风险

### C. 继续 SQLite-only，PG 留生产

- **优点**：零投入
- **缺点**：ADR-0026 欠账永不清；tsvector 分支带病上线

---

## 选择：**方案 A（compose + 标记分层）**

要点：

1. 首个 pg 测试即校验 **0002 迁移**（补齐 Phase 2-9 十二表）与 `Base.metadata` 表名集合一致
2. GIN 表达式索引建在迁移内；测试用 EXPLAIN 断言被使用
3. 中文查询记录性断言：'simple' 配置逐词匹配的**现状**被测试固化（zhparser 是 Phase 11 优化，不是本轮失败）
4. `pytest -m pg` 是显式第二层；`scripts/check.ps1` 提示其存在

---

## 后果

### 正面

- ADR-0026 欠账清偿：tsvector/融合排序/GIN 全部真库验证
- 迁移债清偿：Alembic 与 ORM 对齐（生产可 `upgrade head`）
- 默认 485 套件零扰动

### 负面

- pg 层需手动启动 Docker 才跑（流程写入联调指南）
- 端口 5433 约定需文档化

### 中性

- Phase 11 CI 可直接复用 compose 作为 service 容器

---

## 回滚条件

1. Docker Desktop 在该机器长期不可用 → 评估本机 PG 或远端测试库
