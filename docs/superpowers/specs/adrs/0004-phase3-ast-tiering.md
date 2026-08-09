# ADR-004: AST P1/P2 处理策略

| 字段 | 值 |
|---|---|
| 状态 | 已批准（2026-08-09）|
| 决策者 | 用户 + Claude |
| 影响 Phase | Phase 3 |
| 相关 spec | §5 ASTGuard P1/P2 分级 |

---

## 背景

Phase 2 ASTGuard 仅拦截 P0（os.system / subprocess / socket / ctypes / 外部 pickle.loads）。Phase 3 引入 P1（网络出口 requests / urllib / httpx / aiohttp）和 P2（沙箱外文件访问）。三种处理策略：

1. **拒绝**：命中即 ToolBlockedError
2. **仅提示**：audit + IM 提示，不阻断
3. **自动改写**：替换为 sandbox 安全形式

---

## 选项

### A. 拒绝（与 P0 同等待遇）

- 优点：最安全
- 缺点：误杀合法用例（如 sandbox 内允许的网络白名单访问）

### B. 仅提示（已选）

- 命中 P1/P2 时 audit + IM 提示，不阻断
- 优点：不误伤合法用例；用户可忽略
- 缺点：依赖用户判断；可能漏掉真实危险

### C. 自动改写

- 检测到 requests.get() → 自动改为 sandbox 内 mock 版本
- 优点：兼顾安全与灵活
- 缺点：实现复杂；改写正确性难保证

---

## 选择：**方案 B（仅提示）**

---

## 后果

### 正面

- 不误伤合法用例（网络白名单内的 requests 不被拒）
- 实现简单（仅模式匹配 + 提示）
- 与 P0 行为分层清晰（P0=硬阻塞；P1/P2=软提示）

### 负面

- 用户必须看 IM 提示；可能漏掉
- 没有强制的安全兜底（P1/P2 命中后仍执行）

### 中性

- 安全边界仍由 Docker 沙箱兜底（network_mode + capability drop）
- 真正危险操作（P0）已硬阻塞
- audit_logs 全程记录

---

## 回滚条件

1. P1/P2 提示被用户大量忽略（"为什么不直接拒绝"）
2. 真实环境出现 P1/P2 命中后恶意操作（外部 IP 扫描 / 文件越权）

回滚方案：切到方案 A（P1/P2 也硬阻塞），需扩展 BLOCKED_CALLS 集合。