# ADR-0024: 评论自动同步采用后台轮询（非 webhook、非手动）

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-26）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 9 |
| 相关 spec | §3 评论自动同步 |

---

## 背景

Phase 8 评论同步依赖手动 `/comments-sync`，闭环延迟取决于 owner 记性。Phase 9 要自动化，需决定触发机制。已知约束：本机部署无公网回调条件。

---

## 选项

### A. 后台定时轮询（已选 ✅）

gateway startup 启动 asyncio 后台任务，每 `comment_sync_interval_sec`（默认 300s）执行 `tick()`：扫描 active session 的有效绑定 doc → 逐 doc 调 Phase 8 sync → 触发推送检查。

- **优点**：零部署门槛（无公网回调）；与既有 pull 模式一致；`tick()` 与循环分离可纯单测；间隔可配
- **缺点**：非实时（最坏延迟 = interval）；空转轮询有 API 调用消耗

### B. 飞书事件订阅 webhook 实时推送

comment 事件回调驱动 sync。

- **优点**：真实时
- **缺点**：需公网回调地址 + 事件验签 + 重试去重；本机环境不满足；部署门槛与项目当前阶段不匹配

### C. 维持手动 `/comments-sync`

- **优点**：零改动
- **缺点**：自动闭环目标落空，Phase 9 主题不成立

---

## 选择：**方案 A（后台轮询）**

设计要点：

1. `tick()` 纯同步单轮（session 扫描 + sync + notify），`start_async()` 仅是 `sleep→tick` 薄壳
2. 单 doc 异常隔离（记日志继续），不中断整轮
3. `bind_expires_at` 过期或为空跳过（沿用 Phase 1/3 绑定窗口语义）
4. `create_app(worker=None)` 不传不启动——测试与旧部署零影响
5. 多 session 绑同一 doc 按 doc 去重，防重复 sync

---

## 后果

### 正面

- 手动 `/comments-sync` 可省略，评论落库延迟上限可控（interval）
- 复用 Phase 8 sync 幂等语义，轮询重放零副作用
- 为 §4 推送提供稳定触发点

### 负面

- 空转期有无效 API 调用（被 RateLimiter 约束）
- 多实例部署会重复轮询（MVP 单实例；分布式锁列 Phase 10）

### 中性

- webhook 实时推送、评论分页推迟 Phase 10

---

## 回滚条件

1. 部署环境获得公网回调（上云）→ 切换 webhook（sync 幂等语义不变，可平滑过渡）
2. interval 调到 < 60s 触发限流 → 回退手动模式（worker 是可选注入）
