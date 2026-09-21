# Phase 77 设计：L1 主动式 skill 进化（双入口闭环）

日期：2026-09-21
状态：已批准（用户"好的，执行吧"）

## §0 背景

- **挂账出处**：用户提出 RSI（递归自进化）诉求——仓库已有 Phase 27-29 反应式进化（失败诊断→审批→写回），缺 L1 主动式（成功后复盘沉淀新 skill）。
- **现状**：Phase 27 SkillDiagnoser（[skill_diagnoser.py](../../../orchestrator/coding/skill_diagnoser.py)，275 行）已实现"诊断→`_skill_improve_card` 审批卡→gateway `skill_improve` 回调→apply 写回（.bak 兜底）"全链；触发条件在 [coding_runner.py](../../../orchestrator/coding/coding_runner.py) `_maybe_diagnose_skill`（L412-433，仅失败/连败禁用触发）。
- **本 Phase 定位**：复用同一卡片管线与落盘惯例，新增"成功复盘沉淀新 skill"自动入口 + "/skill install"手动入口，双入口共用同一校验/落盘/审计层。仍 human-in-loop 半自动（L3 全自动明确不做）。

## §1 决策记录（三问定案）

| 决策点 | 选项 | 定案 |
|---|---|---|
| 触发时机 | ①成功后复盘 ②定期批扫 ③两者 | **①**——复用诊断器触发链，信号干净（成功才知什么值得固化），卡管线现成 |
| 落地路径 | ①完整目录+审批卡 ②先建议卡再生成 ③只出草案不落盘 | **①**——复用 Phase 27 卡片，差别仅卡片内容从 patch 变 create |
| 手动 install 是否并入 | ①并入 ②只做自动 ③先手动 | **①**——两入口共用 ~90% 校验/落盘/审批/审计代码，手动 install 还能当自动复盘的联调桩 |

## §2 架构：双入口共用同一落盘层

```
自动入口：/code 任务 success（且触发过滤通过）
  → SkillDistiller.distill(loop_result, task_text)   [新，orchestrator/coding/skill_distiller.py]
  → 产出 create 型 suggestion（含 files 多文件映射）
  → _skill_improve_card(kind=create)  [改，coding_runner.py]
  → gateway skill_improve 回调 → SkillInstaller.install(files)  [新，orchestrator/coding/skill_installer.py]
  → 落盘 skills/<name>/ → 审计 skill_improve_applied
  → 下个 /code 任务 scan 自动生效（skill_loader 热加载，零改动）

手动入口：飞书发 zip 附件 + "/skill install"
  → gateway 消息路由（zip 下载→沙箱临时目录）
  → SkillInstaller.validate_zip()  [新]
  → 同一 _skill_improve_card(kind=create)（来源标注 manual）
  → 同一回调 → 同一 SkillInstaller.install → 同一审计
```

## §3 核心组件

### 3.1 SkillDistiller（新，~150 行）

复用诊断器纪律（Phase 28 教训直接继承）：

- **触发过滤（规则前置，不进 LLM）**：本轮 `tool_events` 中非 skill 工具的 `run_cmd` 调用 ≥2 次且有公共命令前缀/模式（如连续 2+ 次 `python - <<'EOF'` 或同脚本名重复）；否则静默返回 `{"ok": False, "reason": "below threshold"}`。预计触发率 <20%，卡片噪音可控。
- **prompt 归因纪律**：每个建议须逐字引用轨迹中的命令片段（禁笼统归因）；注入既有 skill 的 `name+description` 清单防重复造轮子（复用 `_skill_tool_map`/`_load_tool_defs` 扫描逻辑，抽到公共 helper 或复制小函数——按既有惯例允许小重复）。
- **产出 schema**（与 patch 型区分）：
  ```json
  {"ok": true, "kind": "create", "skill": "<name>",
   "issue": "<复用的重复模式描述>", "fix": "<沉淀价值>",
   "files": {"SKILL.md": "<全文>", "tools.yaml": "<全文>", "run_x.py": "<全文>"}}
  ```
- **幻觉字段白名单**：`files` 键只允许 `SKILL.md`/`tools.yaml`/`*.py`（正则白名单），其余键丢弃（875b280 先例）；单文件 ≤50KB、总文件数 ≤5。
- **name 校验**：`[a-z][a-z0-9_]{1,30}` 且 `skills/<name>/` 不存在（存在则拒收返回 reason，不静默覆盖）。

### 3.2 SkillInstaller（新，~120 行）

统一落盘层（双入口共用）：

- `validate_name(name)`：正则 + 非存在校验。
- `validate_files(files)`：白名单键 + 大小上限 + 内容非空 + SKILL.md 必含 frontmatter `name`/`description`（与 skill_loader L38-46 跳过纪律一致——缺则装了也不生效，前置拦截）。
- `validate_zip(zip_path)`：解压到临时目录，逐条校验——拒绝 `..`/绝对路径/符号链接；目录内须含 SKILL.md；返回规范化 `files` 映射。
- `install(name, files)`：写 `skills/<name>/`（若 name 已存在——仅手动 install 覆盖场景——先整目录 `.bak` 兜底：重命名为 `<name>.bak.<ts>`）；返回 `{"ok": True, "dir": ...}` 或错误 dict（不抛异常，与 diagnoser.apply 同款）。

### 3.3 卡片与回调扩展（改，最小侵入）

- `_skill_improve_card`：suggestion 增 `kind` 字段（`patch` 缺省 / `create`）。`create` 型展示 `files` 键清单 + SKILL.md 前 300 字符预览（替代 patch 预览）；value 内嵌 JSON 长度仍受 `_SUGGESTION_CAPS` 约束（files 总值并入 patch 位 1500 字符上限，超出截断并在卡片标注"截断，批准按完整内容写回"——完整内容不落卡片、由回调侧从 suggestion 暂存取：见下）。
- **完整内容传递**：飞书按钮 value 长度有限，`files` 全文不能内嵌。方案：发卡时把完整 suggestion 暂存进程内 dict `{improve_id: suggestion}`（CodingRunner/Installer 侧），回调按 improve_id 取回（与 code_approval "不落库" 惯例一致，进程重启则卡片失效点不动——toast 提示，可接受）。
- gateway `skill_improve` 回调：`approve` 时按 `kind` 分支——`patch` 走原 `diagnoser.apply`，`create` 走 `installer.install`；审计 detail 增 `kind`/`files` 键清单。

### 3.4 手动 install 入口（gateway 消息路由）

- 触发：消息含 `/skill install` 且带 zip 附件（或附件后紧跟文字）。下载 zip 到 `tmp/`（沙箱临时目录，非 skills/）。
- `validate_zip` 通过 → 发 create 卡（标注"来源：手动 install"）；不通过 → 文本回复具体拒因（缺 SKILL.md / 路径穿越 / frontmatter 缺字段）。
- 附件下载复用现有飞书消息附件链路（仅新增 zip MIME/后缀识别）。

## §4 安全口径

1. 落盘强制 `skills/` 根下；name 正则白名单；拒绝路径穿越（逐条 zip 校验 + name 不含 `/`）。
2. 脚本内容不做语义审计——审批卡展示全文（截断标注）由人把关，与 Phase 27 patch 审批同级。
3. 完整 suggestion 进程内暂存不落库、不审计内容（仅审计动作+元数据），与 code_approval 惯例一致。
4. 覆盖写（仅手动 install 同名）整目录 `.bak.<ts>` 兜底，可回滚。

## §5 测试与验收

- **单测**（预计 +16，基线 1366 → 1382）：
  - SkillDistiller：触发过滤 3（阈值以下静默/达标触发/skill 工具调用不计入）、schema 校验 4（files 白名单/超上限/name 非法/name 已存在）
  - 卡片 kind 分支 3（patch 缺省/create 展示/截断标注）
  - zip 校验 4（正常/路径穿越拒/缺 SKILL.md 拒/符号链接拒）
  - name 冲突 2（create 拒/手动覆盖 .bak）
- **真机验收（三链）**：①手动 zip install 全链（发 zip→卡→批准→/code 命中新 skill）；②自动复盘全链（构造含重复手写 run_cmd 的 /code 任务→自动发卡→批准→落盘生效）；③拒绝路径（坏 zip/name 冲突/无 frontmatter）
- **门禁**：ruff / mypy / pytest 全绿，pre-push 四连门
- **测试总结 #22** 回填

## §6 非目标

1. L2 进化质量自评估（补丁效果度量/自动回滚）——挂账待使用数据积累
2. L3 全自动（去审批卡）——明确不做（幻觉 patch 无把关风险）
3. 定期批处理触发（cron 扫描日志）——本期只做事后即时复盘
4. skill 语义内容审核（LLM 审脚本安全性）——审批责任在人，与存量纪律一致
5. skill_loader 热加载机制改动——零改动（每次任务 scan 一次已满足"装完即生效"）

## §7 风险与缓解

| 风险 | 缓解 |
|---|---|
| 成功任务全量复盘卡片噪音 | §3.1 规则前置过滤（≥2 次重复手写命令才触发 LLM），预计触发率 <20% |
| LLM 幻觉 files 键/schema 外内容 | files 键正则白名单 + 大小/数量上限，幻觉键丢弃（875b280 先例） |
| 飞书按钮 value 长度限制 files 全文 | 进程内暂存 + improve_id 取回（code_approval 同款"不落库"惯例） |
| 手动 install zip 路径穿越/恶意 | §4 逐条校验 + name 白名单 + 落盘根限制 |
| name 冲突覆盖误伤存量 | create 拒已存在；仅手动 install 显式覆盖且整目录 .bak.<ts> 兜底 |
