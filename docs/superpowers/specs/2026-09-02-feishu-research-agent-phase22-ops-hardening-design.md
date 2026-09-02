# Phase 22：运维加固轮 — 设计

- 日期：2026-09-02
- 来源：ROADMAP 持续项 + Phase 20/21 真机验收暴露的运维痛点
- 性质：小改动集合（6 项），单轮交付；无新 Phase 级架构

## 0. 目标与非目标

**目标**：清掉四类真机踩过的坑——双实例复发、bind-doc 无存在性校验、
FastAPI 弃用警告、tiny 数据参数错配；st 镜像瘦身 5GB；ROADMAP 对齐 P21。

**非目标**：沙箱网络白名单与产物回收（仍归 Phase 16 剩余）、planner
repr 串长期方案、bio_workspace 磁盘治理（各自独立立项）。

## 1. ws_client 单实例守卫（pidfile）

**痛点**：四轮真机均出现双实例（venv python + 系统 python 各一），
每次手动 `Get-CimInstance ... Stop-Process` 清理。

**设计**（保守 + --force，用户已选）：

- pidfile：项目根 `.ws_client.pid`（.gitignore 追加），内容仅十进制 pid
- `gateway/ws_client.py` 新增模块级函数 `acquire_single_instance(force: bool) -> None`，
  `main()` 入口最先调用（logging 配置之后）：
  1. pidfile 不存在 → 写入当前 pid，正常继续
  2. 存在 → 读 pid，`ctypes.windll.kernel32.OpenProcess` 探活
     （零新依赖；仅探 pid 存活不校验 cmdline——pid 复用误判概率低，
     代码注释说明取舍）：
     - 进程已死（stale）→ log warning + 覆写接管
     - 进程活着：默认 `logger.error("ws_client already running (pid=N); "
       "use --force to replace")` + `sys.exit(1)`
     - `--force`：`OpenProcess` 拿句柄 `TerminateProcess` 杀旧 → 等 2s
       （轮询探活至消失，上限 5s）→ 覆写接管
  3. 探活失败权限不足等异常一律视为"活着"处理（保守）
- pidfile 清理：`atexit.register` 删除（仅当内容仍是本进程 pid）；
  强杀残留由 stale 接管兜底，不做信号处理
- 参数解析：`main()` 内轻量判断 `sys.argv` 含 `--force`（不引 argparse）
- **单测**（mock `ctypes.windll` 与 pidfile 读写，tmp_path 隔离）：
  无 pidfile 首启写入 / 活实例拒绝（SystemExit 1）/ stale 接管覆写 /
  force 杀旧接管 / atexit 清理不误删他人 pidfile

## 2. bind-doc 存在性校验

**痛点**：P15 验收 doc_id 一字之差（nzb/nkb），bind() 不校验存在性，
绑定"成功"后写回时才 1770002 not found。

**设计**：`orchestrator/bind_doc_service.py` 的 `bind()` 在正则校验通过
后、`session_service.bind_doc` 之前加探活：

- `self.doc_adapter` 非 None 时，调其文档读取接口验证 doc_id 存在
  （方法名以 DocAdapter 现有公开方法为准——实现时读
  `orchestrator/adapters/` 下 DocAdapter 定义取 get/读取类方法；
  wiki 分支已在 `_resolve_wiki` 走 doc_adapter，天然覆盖）
- 校验失败（LarkCLIError/异常）→ `raise BindDocInvalidError(
  "document not found: {doc_id}——请检查 doc_id 或链接是否正确")`
- doc_adapter 为 None（未配置 SDK 通道，如纯单测环境）→ 跳过探活
  保持现状（与 wiki 分支的降级语义一致）
- IM 侧无需改动：`_handle_bind` 已捕获 BindDocInvalidError 回复用户
- **单测**：mock doc_adapter 读接口抛错 → BindDocInvalidError 含
  "not found"；正常返回 → 绑定照常；doc_adapter None → 跳过

## 3. FastAPI on_event → lifespan 迁移

**痛点**：8 条 PydanticDeprecatedSince20/FastAPI 弃用警告（启动噪音，
掩盖新警告）。

**设计**：`gateway/app.py` 的 `@app.on_event("startup")` /
`@app.on_event("shutdown")` 迁 `asynccontextmanager lifespan(app)`，
FastAPI(...) 构造传 `lifespan=`。行为等价迁移不改逻辑；全量单测回归
（ws_client 测试不经过 FastAPI 装配的保持不动）。

## 4. sc_qc/st_qc description 补小数据参数指导

**痛点**：批③真机 planner 按真实 scRNA 惯例选 `min_genes=600`，tiny
参考中位 66 基因全滤光（SC_QC_OVERFILTERED）；错误消息虽含调参指导
但 planner 一次性规划无法自纠。

**设计**：`orchestrator/tools/builtin/l3_singlecell.py` 的 sc_qc 与
`l3_spatial.py` 的 st_qc ToolSpec description 各追加一句：

> 小规模/测试数据每细胞（spot）基因数低（可能仅几十），min_genes
> 过大会全滤光——若失败，错误消息含 genes/cell 分布（median/p90/max），
> 按 median 以下调低重试。

纯 description 变更（planner prompt 自动带入），不改 handler/schema。

## 5. st 镜像 torch CPU 瘦身

**痛点**：批③ torch 走 CUDA 回退（2.13.0+cu130），镜像 7.17GB，
其中 ~5GB 为无用 CUDA 依赖。

**设计**：`sandbox/st.Dockerfile` torch 层改固定 CPU 版本：

- 实现时先 `Invoke-WebRequest` 或浏览器查 tuna pytorch-wheels/cpu 目录
  可用的 cp312 wheel 版本，取最新（如 `torch==2.x.y+cpu`）
- `pip install --no-cache-dir -i 清华源 -f tuna/cpu/ "torch==<ver>+cpu"`
  失败（目录无 cp312）则保留现状并在 Dockerfile 注释记录原因
- 重建后验证：容器内 `import cell2location` + `torch.cuda.is_available()
  == False` + 重跑冒烟 deconvolve 步（356s 量级不回归）
- 预期 7.17GB → ~2.5GB（-5GB）

## 6. ROADMAP 更新至 P21

`docs/ROADMAP.md`：Phase 20 后续扩展勾选 st_*（转 Phase 21 记录三批
收官）；持续项勾掉本轮完成项（pidfile/bind-doc/lifespan）；变更记录
追加两行（Phase 21 收官、Phase 22 运维加固轮）。

## 7. 验收标准

- 单测：全量 passed（新增 ≥7 用例：守卫 5 + bind 校验 3 左右）
- 真机：双起 ws_client 第二个拒绝退出（--force 可接管）；其余项
  单测/冒烟覆盖，无需大流程配合
- 冒烟：deconvolve 步在新镜像上耗时与结果量级不回归
- 交付：1 spec + 1 plan + 按 TDD 分任务提交

## 8. 风险

| 风险 | 对策 |
|---|---|
| tuna pytorch-wheels 无 cp312 CPU wheel | 保留 CUDA 版现状 + 注释记录，不阻塞本轮 |
| OpenProcess 探活权限 | 异常一律按"活着"保守处理 |
| lifespan 迁移遗漏 startup 钩子 | 迁移前后对比 app.routes/启动日志 + 全量回归 |
| description 变更影响 planner 行为 | 仅追加提示语，无 schema/默认值变化 |
