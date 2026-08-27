# ADR-0028: 依赖锁定采用 .venv + requirements-lock.txt（钉死 Python 3.12）

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-26）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 10 |
| 相关 spec | §3 依赖锁定 |

---

## 背景

本机存在 Python 3.10 / 3.12 双解释器。Phase 8 实施中 3.12 环境缺 `python-ulid` / `respx`（另一环境曾装过），出现"同机不同解释器依赖漂移"。需决定环境锁定方式。

---

## 选项

### A. 项目 .venv + requirements-lock.txt（已选 ✅）

项目根建 `.venv`（Python 3.12.10），`pip freeze` 全量锁定（含传递依赖精确版本）；README 注明清华源复现命令；版本钉死 3.12。

- **优点**：零迁移成本（pip 原生）；锁文件可 diff 可 review；任何机器 `pip install -r` 即复现；`.gitignore` 已忽略 venv
- **缺点**：跨平台锁（Linux CI）可能遇个别包差异——Phase 11 CI 时再验证

### B. 迁移 uv / poetry

现代包管理（锁文件 + 虚拟环境一体）。

- **优点**：体验最好，锁更严格
- **缺点**：迁移成本 + Windows 兼容需验证 + 团队心智切换；当前痛点用 pip 锁已可解

### C. 只补 requirements.txt（区间版本）

- **优点**：最简
- **缺点**：不锁传递依赖，漂移问题依旧（治标不治本）

---

## 选择：**方案 A（.venv + freeze 锁）**

要点：

1. Python **3.12.10** 钉死（当前项目实际解释器）
2. 锁文件命名 `requirements-lock.txt`（与概念性 `pyproject.toml` dependencies 区分）
3. 安装源：清华 pypi 镜像（用户规范）
4. README 环境章节成为唯一入口（创建/复现/排障）

---

## 后果

### 正面

- 依赖漂移根因消除；Phase 8 式"环境咬人"不再
- 锁文件进 git，任何时点可审计依赖变化

### 负面

- 每次 `pip install` 新包后需手动重新 freeze（流程约定写入 README）

### 中性

- uv/poetry 可在 Phase 11+ 平滑评估（锁文件语义已建立）

---

## 回滚条件

1. Phase 11 CI 出现跨平台锁冲突 → 评估 uv（其锁原生跨平台）
