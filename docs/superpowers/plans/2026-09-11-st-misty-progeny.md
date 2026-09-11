# st_misty PROGENy 通路视图实施计划（Phase 50）

> **For agentic workers:** 按 Task 顺序执行，TDD：红灯→绿灯→脚本层→冒烟→门禁。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** st_misty 扩 `extra_mode=hvg|progeny` 参数，progeny 模式用 decoupler MLM 算 PROGENy 14 通路活性作 extra 视图，断网纪律不破（构建期快照）。

**Architecture:** 宿主 handler 增 extra_mode 透传 → st 镜像增 decoupler pip 层 + PROGENy TSV 快照层（fetch_gene_pos 先例）→ misty.py 增 progeny 分支（dc.mt.mlm → obsm["score_mlm"] → extra AnnData），下游 genericMistyData 一字不改。

**Tech Stack:** decoupler 2.2.0（`dc.op.progeny` 构建期拉模型 / `dc.mt.mlm` 运行期离线打分）、liana 1.10.0（既有）、pytest、docker。

**Spec:** `docs/superpowers/specs/2026-09-11-st-misty-progeny-design.md`（含两轮探针 API 事实）

**探针实测事实（2026-09-11，全部断网验证过）：**
- `dc.op.progeny(organism="human", top=500)` → DataFrame(6463, [source,target,weight,padj])，14 通路
- `dc.mt.mlm(adata, net, tmin=5, verbose=True)` → 返回 None，写 `obsm["score_mlm"]`（n×14 DataFrame）+ `obsm["padj_mlm"]`；`dc.mlm` 不存在于 2.2.0
- 断网 E2E：50 spot×5276 基因 ~10s；注入 EGFR 信号 corr=0.998 回收

---

### Task 1: 红灯——st_misty 注册测试增 extra_mode 断言

**Files:**
- Test: `tests/unit/test_l3_st_misty.py`

- [ ] **Step 1: 改两例既有断言 + 新增两例**

`test_st_misty_forwards_params` 的 args dict 断言增 `extra_mode` 键：

```python
def test_st_misty_forwards_params(runner, reg):
    """全参数透传 + st 镜像 + /opt/st_tools + timeout 1800。"""
    reg.get("st_misty").handler(
        dataset_ref="abc123", n_hvg=80, bandwidth=250.0)
    args, kw = runner.run.call_args
    assert args[0] == "misty"
    assert args[1] == {"dataset_id": "abc123", "n_hvg": 80,
                       "bandwidth": 250.0, "extra_mode": "hvg"}
    assert kw["image"] == ST_IMAGE
    assert kw["script_dir"] == "/opt/st_tools"
    assert kw["timeout_sec"] == 1800
```

文件末尾新增两例：

```python
def test_st_misty_extra_mode_default(runner, reg):
    """extra_mode 默认 hvg（向后兼容）并透传进 args payload。"""
    reg.get("st_misty").handler(dataset_ref="abc123")
    args, _ = runner.run.call_args
    assert args[1]["extra_mode"] == "hvg"


def test_st_misty_extra_mode_forwarded(runner, reg):
    """extra_mode=progeny 透传进 args payload。"""
    reg.get("st_misty").handler(dataset_ref="abc123",
                                extra_mode="progeny")
    args, _ = runner.run.call_args
    assert args[1]["extra_mode"] == "progeny"
```

`test_st_misty_registered` 末尾增一行：

```python
    assert props["extra_mode"]["default"] == "hvg"
```

- [ ] **Step 2: 跑测试确认红灯**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_st_misty.py -q --basetemp=I:\飞书agent\.pytest_tmp`
Expected: 3 FAIL——forwards_params（dict 不等，缺 extra_mode 键）/ extra_mode_default / extra_mode_forwarded（TypeError: unexpected keyword）；
`test_st_misty_registered` 同红（props 无 extra_mode 键）。

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_l3_st_misty.py
git commit -m "test(st): st_misty extra_mode 红灯断言（Phase 50 Task1）"
```

### Task 2: 绿灯——l3_spatial handler + ToolSpec 增 extra_mode

**Files:**
- Modify: `orchestrator/tools/builtin/l3_spatial.py`（st_misty handler 237-251 行附近 + ToolSpec 559-585 行附近）

- [ ] **Step 1: handler 增参透传**

```python
    def st_misty(*, dataset_ref: str, n_hvg: int = 50,
                 bandwidth: float = 0,
                 extra_mode: str = "hvg") -> dict[str, Any]:
        """多视图空间建模（liana MISTy）：组成=intra，HVG/通路=juxta/para。"""
        try:
            out = runner.run(
                "misty", {
                    "dataset_id": dataset_ref,
                    "n_hvg": n_hvg,
                    "bandwidth": bandwidth,
                    "extra_mode": extra_mode,
                }, image=st_image, script_dir=_ST_SCRIPT_DIR,
                timeout_sec=1800)
        except BioRunError as e:
            return _err(e)
        out.pop("ok", None)
        return out
```

- [ ] **Step 2: ToolSpec properties 增 extra_mode**

`"bandwidth"` 键后插入：

```python
                "extra_mode": {"type": "string", "default": "hvg",
                               "enum": ["hvg", "progeny"],
                               "description": "extra 视图来源：hvg=top HVG "
                                              "表达；progeny=PROGENy 14 通路"
                                              "活性（decoupler MLM，离线）"},
```

- [ ] **Step 3: 跑测试确认绿灯**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_st_misty.py -q --basetemp=I:\飞书agent\.pytest_tmp`
Expected: 7 passed

- [ ] **Step 4: Commit**

```bash
git add orchestrator/tools/builtin/l3_spatial.py
git commit -m "feat(st): st_misty 增 extra_mode 参数（Phase 50 Task2）"
```

### Task 3: st.Dockerfile 两层 + misty.py progeny 分支

**Files:**
- Create: `sandbox/fetch_progeny.py`
- Modify: `sandbox/st.Dockerfile`（liana 层后插入两层）
- Modify: `sandbox/st_tools/misty.py`

- [ ] **Step 1: 写构建期快照脚本 `sandbox/fetch_progeny.py`**

```python
"""构建期脚本：decoupler OmniPath PROGENy human top500 → TSV（st 镜像层内运行）。

dc.op.progeny 联网拉取 14 通路权重表（source/target/weight/padj），写
/opt/progeny/progeny_human_top500.tsv（~6463 行，运行期断网可用）。
下载失败非零退出 → docker build 报错重试（fetch_gene_pos.py 同语义）。
"""
from __future__ import annotations

import os

OUT_DIR = "/opt/progeny"
OUT_PATH = os.path.join(OUT_DIR, "progeny_human_top500.tsv")


def main() -> None:
    """decoupler 拉 PROGENy 模型并快照为 TSV（14 通路断言兜底）。"""
    import decoupler as dc
    net = dc.op.progeny(organism="human", top=500)
    os.makedirs(OUT_DIR, exist_ok=True)
    net.to_csv(OUT_PATH, sep="\t", index=False)
    n_pw = int(net["source"].nunique())
    print(f"wrote {len(net)} rows / {n_pw} pathways to {OUT_PATH}")
    assert n_pw == 14, f"通路数异常: {n_pw}"


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: st.Dockerfile liana 层后插入两层**

```dockerfile
# Phase 50 st_misty PROGENy 通路视图：decoupler + OmniPath 模型快照
# （两层分离——改快照脚本不重装 pip；快照需构建期网络，运行期断网读 TSV）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple decoupler==2.2.0 \
    && python -c "import decoupler; print('decoupler', decoupler.__version__)"
COPY fetch_progeny.py /tmp/fetch_progeny.py
RUN python /tmp/fetch_progeny.py && rm /tmp/fetch_progeny.py
```

- [ ] **Step 3: misty.py 增 progeny 分支**

docstring 首行块改为：

```python
"""st_misty：多视图空间建模（Phase 49 liana MISTy；Phase 50 PROGENy 视图）。

stdin: {"dataset_id": ..., "n_hvg": 50, "bandwidth": 0, "extra_mode": "hvg"}
intra=deconv.h5ad 细胞型组成（obsm["q05_cell_abundance_w_sf"] 去前缀，
缺失报 ST_MISTY_NO_DECONV）；extra 二选一（extra_mode）：hvg=processed
top HVG 表达（默认，离线安全）；progeny=PROGENy 14 通路活性（decoupler
MLM，模型为构建期快照 /opt/progeny/progeny_human_top500.tsv，运行期
断网）。两者附 obsm["spatial"] 后 genericMistyData 自建 juxta
（n_neighs=6 紧邻）+ para（bandwidth 半径）视图，RandomForestModel
逐目标建模（n_jobs=2 内存纪律）。bandwidth=0 → 5×中位近邻距
（tool-misty l=5 口径）。
产物落 /ws/{ds}/misty/：视图贡献热图 + para 视图 target×predictor
重要性热图 + target_metrics/interactions 两个全量 csv。
容器探针（2026-09-11 liana 1.10.0 实测）：uns["target_metrics"] 列
target/intra_R2/multi_R2/gain_R2/intra/juxta/para（后三=视图贡献）；
uns["interactions"] 列 target/predictor/view/importances，view ∈
{intra,juxta,para}；纯噪声数据 gain_R2=0 甚至可为负（CV R² 性质）。
decoupler 2.2.0 实测：dc.op.progeny(human, top=500)→6463 行 14 通路；
dc.mt.mlm(adata, net, tmin=5) 返回 None，写 obsm["score_mlm"]（n×14
DataFrame）——dc.mlm 不存在，方法在 dc.mt 命名空间。
"""
```

常量区（VIEW_COLS 后）增：

```python
PROGENY_TSV = Path("/opt/progeny/progeny_human_top500.tsv")
```

`_auto_bandwidth` 后新增函数：

```python
def _load_progeny_extra(adata: Any, intra: Any,
                        coords: np.ndarray) -> Any:
    """PROGENy MLM 通路活性 → extra AnnData（14 通路列，附 spatial）。

    net=构建期快照 TSV（运行期断网）；dc.mt.mlm 写 obsm['score_mlm']
    （spot×14 DataFrame，列=通路）。net 靶基因∩数据 var_names <100 →
    INVALID_INPUT（基因名非人类 symbol/物种不符）。
    """
    import anndata as ad
    if not PROGENY_TSV.exists():
        fail("ST_MISTY_NO_PROGENY",
             f"{PROGENY_TSV} 缺失（镜像快照层异常，重建 st 镜像）")
        raise SystemExit(1)
    net = pd.read_csv(PROGENY_TSV, sep="\t")
    n_overlap = len(set(net["target"]) & set(adata.var_names))
    if n_overlap < 100:
        fail("INVALID_INPUT",
             f"PROGENy 靶基因与数据交集过少: {n_overlap} (<100，"
             "基因名需为人类 symbol)")
        raise SystemExit(1)
    sub = adata[intra.obs_names, :].copy()
    import decoupler as dc
    dc.mt.mlm(sub, net, tmin=5, verbose=False)
    scores = sub.obsm["score_mlm"].astype(np.float32)
    extra = ad.AnnData(X=scores.to_numpy(),
                       var=pd.DataFrame(index=scores.columns))
    extra.obs_names = intra.obs_names
    extra.obsm["spatial"] = coords[
        adata.obs_names.isin(intra.obs_names)]
    return extra
```

main() 中 `bandwidth` 校验后增 extra_mode 校验：

```python
    extra_mode = str(args.get("extra_mode", "hvg"))
    if extra_mode not in ("hvg", "progeny"):
        fail("INVALID_INPUT",
             f"extra_mode={extra_mode!r} 非法（需 hvg|progeny）")
        raise SystemExit(1)
```

HVG extra 构建块改为二选一分支（`n_pred` 供 emit 用，替代原 `len(hvgs)`）：

```python
    # extra 二选一：hvg=top HVG 表达；progeny=PROGENy 通路活性
    import anndata as ad
    if extra_mode == "progeny":
        extra = _load_progeny_extra(adata, intra, coords)
        n_pred = int(extra.n_vars)
    else:
        hvgs: list[str]
        if "highly_variable" in adata.var.columns:
            hvgs = adata.var.index[
                adata.var["highly_variable"]].tolist()[:n_hvg]
        else:
            x = adata.X
            x_var = np.asarray(x.var(axis=0)).ravel()
            hvgs = adata.var.index[
                np.argsort(x_var)[::-1][:n_hvg]].tolist()
        if len(hvgs) < 10:
            fail("INVALID_INPUT", f"可用 HVG 过少（{len(hvgs)} < 10）")
            raise SystemExit(1)
        sub = adata[intra.obs_names, hvgs]
        extra = ad.AnnData(
            X=np.asarray(
                sub.X.todense() if hasattr(sub.X, "todense") else sub.X,
                dtype=np.float32),
            var=pd.DataFrame(index=hvgs))
        extra.obs_names = intra.obs_names
        extra.obsm["spatial"] = coords[
            adata.obs_names.isin(intra.obs_names)]
        n_pred = len(hvgs)
```

emit 三处改：`"n_predictors": int(len(hvgs))` → `"n_predictors": n_pred`；
`"dataset_ref"` 行后增 `"extra_mode": extra_mode,`；note 改：

```python
        "note": "liana MISTy（genericMistyData intra/juxta/para + "
                "RandomForestModel）；intra=细胞型组成，extra="
                "top HVG 表达或 PROGENy 通路活性（extra_mode）；"
                "importance=Gini 下降",
```

- [ ] **Step 4: mypy + 单测复验**

Run: `.venv\Scripts\python.exe -m mypy sandbox/st_tools/misty.py`
Expected: Success: no issues found
Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_st_misty.py tests/unit/test_l3_spatial.py -q --basetemp=I:\飞书agent\.pytest_tmp`
Expected: all passed（注册测试不受容器脚本影响）

- [ ] **Step 5: Commit**

```bash
git add sandbox/fetch_progeny.py sandbox/st.Dockerfile sandbox/st_tools/misty.py
git commit -m "feat(st): misty.py progeny 分支 + decoupler/快照镜像层（Phase 50 Task3）"
```

### Task 4: 镜像重建 + 断网容器冒烟

**Files:**
- Modify: `scripts/_smoke_st_misty.py`

- [ ] **Step 1: 冒烟脚本增 progeny 段**

`DS_NODEC` 常量行后增：

```python
DS_PROG = "stmistyprog"
```

`_BUILDER` 字符串末尾（`print("built", n)` 前）增第三数据集（真实
PROGENy 基因名 + EGFR 通路信号注入；信号直接加在最终表达矩阵上——
教训十六：无中间变换时计数空间注入即有效）：

```python
# stmistyprog：真实 PROGENy 基因名，EGFR target 按 weight×tumor 梯度
# 注入（探针 B 同款，corr=0.998 实测可回收）
net = pd.read_csv("/opt/progeny/progeny_human_top500.tsv", sep="\t")
egfr = net[net["source"] == "EGFR"][["target", "weight"]].drop_duplicates(
    "target")
others = sorted(set(net["target"]) - set(egfr["target"]))
fill = np.random.default_rng(7).choice(others, size=150,
                                       replace=False).tolist()
genes2 = egfr["target"].tolist() + fill
g2idx = pd.Index(genes2)
X2 = np.random.default_rng(0).normal(0, 1, (n, len(genes2))).astype(
    np.float32)
cols = g2idx.get_indexer(egfr["target"])
X2[:, cols] += (tumor_frac * 3).astype(np.float32)[:, None] * \
    egfr["weight"].to_numpy(dtype=np.float32)[None, :] * 0.5
proc3 = ad.AnnData(X=X2, obs=pd.DataFrame(index=barcodes),
                   var=pd.DataFrame(index=genes2))
proc3.obsm["spatial"] = coords
proc3.write_h5ad("/ws/stmistyprog/processed.h5ad")
dec.write_h5ad("/ws/stmistyprog/deconv.h5ad")
```

`build_dataset()` 的 mkdir 行后增 `(WS / DS_PROG).mkdir(parents=True, exist_ok=True)`。

主流程 NO_DECONV 断言后、收尾 print 前增：

```python
# progeny 主跑：extra=PROGENy 14 通路活性，EGFR 信号应回收
p = run_misty(DS_PROG, extra_mode="progeny", bandwidth=2.0)
assert p["ok"], p
assert p["extra_mode"] == "progeny", p
assert p["n_predictors"] == 14, p
assert p["n_targets"] == 3, p
with open(WS / DS_PROG / "misty/misty_interactions.csv",
          newline="") as fh:
    prows = list(_csv.DictReader(fh))
pt = [r for r in prows
      if r["view"] == "para" and r["target"] == "Tumor"]
pt.sort(key=lambda r: float(r["importances"]), reverse=True)
ptop3 = {r["predictor"] for r in pt[:3]}
assert "EGFR" in ptop3, f"EGFR not in para top3 for Tumor: {ptop3}"

# 基因名零交集（stmistysmoke 的 g0..g59 非 symbol）→ INVALID_INPUT
no_ov = run_misty(DS, extra_mode="progeny")
assert not no_ov["ok"] and no_ov["error_code"] == "INVALID_INPUT", no_ov

# 非法 extra_mode → INVALID_INPUT
bad_mode = run_misty(DS, extra_mode="bogus")
assert not bad_mode["ok"] and bad_mode["error_code"] == "INVALID_INPUT", \
    bad_mode
```

收尾 print 改：

```python
print("SMOKE OK | targets:", o["n_targets"],
      "| mean_gain_R2:", o["mean_gain_R2"],
      "| g0 recovered in Tumor para top3",
      "| EGFR recovered in progeny para top3 (n_predictors=%d)"
      % p["n_predictors"],
      "| INVALID_INPUT/NO_DECONV/bad-mode/no-overlap rejected")
```

- [ ] **Step 2: 重建 st 镜像（decoupler+快照层实跑）**

Run: `docker build -t feishu-research-agent/bio:st-cpu-latest sandbox -f sandbox/st.Dockerfile`
Expected: 构建成功，日志含 `decoupler 2.2.0` 与 `wrote 6463 rows / 14 pathways`

- [ ] **Step 3: 跑断网冒烟**

Run: `.venv\Scripts\python.exe scripts\_smoke_st_misty.py`
Expected: `SMOKE OK | ... | EGFR recovered in progeny para top3 (n_predictors=14) | ...`

- [ ] **Step 4: 清理冒烟产物与探针残留**

```bash
Remove-Item -Recurse -Force I:\飞书agent\bio_workspace\stmistysmoke, I:\飞书agent\bio_workspace\stmistynodec, I:\飞书agent\bio_workspace\stmistyprog, I:\飞书agent\bio_workspace\_builder_stmisty, I:\飞书agent\.probe_progeny
docker rmi st-progeny-probe
```

- [ ] **Step 5: Commit**

```bash
git add scripts/_smoke_st_misty.py
git commit -m "test(st): st_misty progeny 模式断网冒烟（EGFR 信号回收）（Phase 50 Task4）"
```

### Task 5: 全量门禁 + 文档 + 推送 CI

**Files:**
- Modify: `docs/ROADMAP.md`（追加 Phase 50 行）
- Modify: `测试总结+2026-09-09T01-55-00.md`（追加十七）

- [ ] **Step 1: 全量回归（统一 `-m "not pg"` 口径）**

Run: `.venv\Scripts\python.exe -m pytest -q -m "not pg" --basetemp=I:\飞书agent\.pytest_tmp > reg_out.txt 2>&1; Select-String -Path reg_out.txt -Pattern "deselected"`
Expected: `1267 passed, 6 deselected`（1265 + Task1 新增 2 例）；看完摘要后删 reg_out.txt

- [ ] **Step 2: ROADMAP 追加 Phase 50 行 + 测试总结追加十七**

ROADMAP 行要点：st_misty 扩 extra_mode 参数（非新工具）；decoupler
2.2.0 + 构建期 OmniPath 快照（fetch_gene_pos 先例，断网纪律不破）；
`dc.mt.mlm` 命名空间坑（dc.mlm 不存在于 2.2.0）；探针两轮前置；
冒烟 EGFR corr=0.998 信号回收 + 三错误路径；全量 1267 passed（同
口径恰 +2）。

测试总结十七要点：范围/实施/验证/教训四节；教训记"decoupler v2
API 挪命名空间（dc.mt.mlm）——探针先行的又一次回本"与"冒烟复用
既有假名数据集作零交集错误路径对照（一份数据两用）"。

- [ ] **Step 3: Commit + 推送**

```bash
git add docs/ROADMAP.md "测试总结+2026-09-09T01-55-00.md"
git commit -m "docs: Phase 50 ROADMAP + 测试总结追加十七"
git push
```

- [ ] **Step 4: CI 轮询至 success**

git credential 取 PAT → GitHub API 查最新 workflow run，轮询至
`conclusion=success`。

---

## Self-Review

**Spec coverage：**
- §2 快照两层 → Task 3 Step 1/2 ✓；API 事实 → plan 头部 + docstring ✓
- §3 progeny 数据流/防御（overlap<100 INVALID_INPUT、NO_PROGENY）
  → Task 3 Step 3 ✓（NO_PROGENY 分支含在 _load_progeny_extra）
- §4 extra_mode 参数（默认 hvg/非法 INVALID_INPUT）→ Task 2 + Task 3 Step 3 ✓
- §5 emit extra_mode 回显/n_predictors=14 → Task 3 Step 3 + Task 4 断言 ✓
- §6 错误码四场景 → 非法 mode/零交集 Task 4 冒烟；NO_DECONV 沿用；
  NO_PROGENY 为镜像层防御（正常构建必存在，不单独冒烟——fetch
  层 assert 14 通路已在构建期兜底）
- §7 单测 +2 → Task 1 ✓；冒烟三错误路径 → Task 4 ✓；1265+2=1267 → Task 5 ✓

**Placeholder scan：** 无 TBD/TODO；所有代码步骤含完整代码。

**Type consistency：** `extra_mode` 全链统一（handler 参数 → args
payload → read_args 键 → emit 键）；`_load_progeny_extra(adata,
intra, coords)` 签名与 main() 调用一致；`n_pred` int 与 emit 一致；
PROGENY_TSV Path 与 `_load_progeny_extra`/builder 读取路径一致。
