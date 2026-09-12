# st_misty TF/collectri 调控子视图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** st_misty 扩 `extra_mode="tf"` 第三值——CollecTRI TF 活性（decoupler MLM）作 extra 视图，断网纪律不破（构建期快照 TSV）。

**Architecture:** Phase 50 progeny 模板同构复用：构建期 `fetch_collectri.py` 快照 `/opt/collectri/collectri_human.tsv`（42990 边/1185 TFs）；容器 misty.py 加 `_load_collectri_extra` 分支（overlap<500 INVALID_INPUT、TSV 缺失 ST_MISTY_NO_COLLECTRI）；para 热图 tf 模式 top30 截断（csv 全量）；pip 零改动。

**Tech Stack:** decoupler 2.2.0（已在镜像）/ liana 1.10.0 / Docker 构建期快照 / pytest TDD

**Spec:** `docs/superpowers/specs/2026-09-12-st-misty-tf-design.md`（已批准，探针数据背书：DDIT3 回收 corr=0.9901、772 TFs tmin=5、MLM 11.8s、MISTy 外推 ~150s）

**门禁口径：** `pytest -q -m "not pg"`；基线 1272，预期 **1274**（+2）；pre-push=scripts/check.ps1（ruff→mypy→pytest --cov）

---

### Task 1: 红灯——注册测试 2 新例

**Files:**
- Modify: `tests/unit/test_l3_st_misty.py`（文件尾部追加）

- [ ] **Step 1: 追加 2 个失败测试**

在 `test_st_misty_extra_mode_forwarded` 之后追加：

```python
def test_st_misty_extra_mode_tf_forwarded(runner, reg):
    """extra_mode=tf 透传进 args payload。"""
    reg.get("st_misty").handler(dataset_ref="abc123", extra_mode="tf")
    args, _ = runner.run.call_args
    assert args[1]["extra_mode"] == "tf"


def test_st_misty_extra_mode_enum(reg):
    """extra_mode enum 含 tf（CollecTRI 调控子视图）。"""
    props = reg.get("st_misty").parameters["properties"]
    assert props["extra_mode"]["enum"] == ["hvg", "progeny", "tf"]
```

- [ ] **Step 2: 跑测试确认红灯**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_st_misty.py -q`
Expected: `test_st_misty_extra_mode_enum` FAIL（enum 现为 `["hvg","progeny"]`）；`test_st_misty_extra_mode_tf_forwarded` PASS（handler 无条件透传，属"红得只有一半"，先例 Phase 50 同形态）

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_l3_st_misty.py
git commit -m "test(st_misty): extra_mode=tf 注册测试（红灯）"
```

---

### Task 2: 绿灯——l3_spatial.py ToolSpec enum 扩 tf

**Files:**
- Modify: `orchestrator/tools/builtin/l3_spatial.py:599-603`（st_misty ToolSpec 的 extra_mode 属性）

- [ ] **Step 1: enum 加 "tf" + 描述补 CollecTRI**

将：

```python
                "extra_mode": {"type": "string", "default": "hvg",
                               "enum": ["hvg", "progeny"],
                               "description": "extra 视图来源：hvg=top HVG "
                                              "表达；progeny=PROGENy 14 通路"
                                              "活性（decoupler MLM，离线）"},
```

改为：

```python
                "extra_mode": {"type": "string", "default": "hvg",
                               "enum": ["hvg", "progeny", "tf"],
                               "description": "extra 视图来源：hvg=top HVG "
                                              "表达；progeny=PROGENy 14 通路"
                                              "活性；tf=CollecTRI TF 活性"
                                              "（decoupler MLM，离线）"},
```

注意：old_str 锚定须含 `"bandwidth": {"type": "number", ...},` 前行作独有上下文（教训十八：SearchReplace 跨区误配先例），编辑后读回验证 st_load/st_misty 块名未受波及。

- [ ] **Step 2: 跑测试确认全绿**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_l3_st_misty.py tests/unit/test_l3_spatial.py -q`
Expected: 全 PASS（14 工具计数不变）

- [ ] **Step 3: Commit**

```bash
git add orchestrator/tools/builtin/l3_spatial.py
git commit -m "feat(st_misty): extra_mode enum 扩 tf（CollecTRI 调控子视图）"
```

---

### Task 3: 容器侧——fetch_collectri.py + misty.py tf 分支 + Dockerfile 快照层 + 镜像重建

**Files:**
- Create: `sandbox/fetch_collectri.py`
- Modify: `sandbox/st_tools/misty.py`（常量/新函数/校验/分支/热图 top_n/emit 键）
- Modify: `sandbox/st.Dockerfile`（progeny 快照层后加一层）

- [ ] **Step 1: 新建 `sandbox/fetch_collectri.py`（fetch_progeny.py 同构）**

```python
"""构建期脚本：decoupler CollecTRI human → TSV（st 镜像层内运行）。

dc.op.collectri 联网拉取 TF-靶基因权重表（source/target/weight/
resources/references/sign_decision），写 /opt/collectri/
collectri_human.tsv（~42990 行 / 1185 TFs，运行期断网可用）。
下载失败非零退出 → docker build 报错重试（fetch_progeny.py 同语义）。
"""
from __future__ import annotations

import os

OUT_DIR = "/opt/collectri"
OUT_PATH = os.path.join(OUT_DIR, "collectri_human.tsv")


def main() -> None:
    """decoupler 拉 CollecTRI 模型并快照为 TSV（TF 数断言兜底）。"""
    import decoupler as dc
    net = dc.op.collectri(organism="human")
    os.makedirs(OUT_DIR, exist_ok=True)
    net.to_csv(OUT_PATH, sep="\t", index=False)
    n_tf = int(net["source"].nunique())
    print(f"wrote {len(net)} rows / {n_tf} TFs to {OUT_PATH}")
    assert n_tf > 1000, f"TF 数异常: {n_tf}"


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: misty.py 加常量与 `_load_collectri_extra`**

在 `PROGENY_TSV = ...` 行后加：

```python
COLLECTRI_TSV = Path("/opt/collectri/collectri_human.tsv")
```

在 `_load_progeny_extra` 函数后加（同构，差异=错误码/overlap 500）：

```python
def _load_collectri_extra(adata: Any, intra: Any,
                          coords: np.ndarray) -> Any:
    """CollecTRI MLM TF 活性 → extra AnnData（~772 TF 列，附 spatial）。

    net=构建期快照 TSV（运行期断网）；dc.mt.mlm 写 obsm['score_mlm']
    （spot×n_TF DataFrame，列=TF，tmin=5 过滤后 ~772）。net 靶基因∩
    数据 var_names <500 → INVALID_INPUT（探针实证：overlap=100 时
    MLM 因邻接矩阵秩 < 协变量数断言失败；真实 Visium 交集数千）。
    """
    import anndata as ad
    if not COLLECTRI_TSV.exists():
        fail("ST_MISTY_NO_COLLECTRI",
             f"{COLLECTRI_TSV} 缺失（镜像快照层异常，重建 st 镜像）")
        raise SystemExit(1)
    net = pd.read_csv(COLLECTRI_TSV, sep="\t")
    n_overlap = len(set(net["target"]) & set(adata.var_names))
    if n_overlap < 500:
        fail("INVALID_INPUT",
             f"CollecTRI 靶基因与数据交集过少: {n_overlap} (<500，"
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

- [ ] **Step 3: `_para_interactions_heatmap` 加 top_n 截断**

签名与函数体改为：

```python
def _para_interactions_heatmap(inter: pd.DataFrame,
                               png_path: Path,
                               top_n: int = 0) -> pd.DataFrame:
    """para 视图 target×predictor importance 热图。返回透视矩阵。

    top_n>0 且预测子数超出时，只画 importance 总和 top top_n 列
    （tf 模式 ~772 列不可读；csv 仍全量）。
    """
    import matplotlib.pyplot as plt
    para = inter[inter["view"] == "para"]
    mat = para.pivot_table(index="target", columns="predictor",
                           values="importances", fill_value=0.0)
    if top_n > 0 and mat.shape[1] > top_n:
        keep = mat.sum(axis=0).sort_values(
            ascending=False).head(top_n).index
        mat = mat[keep]
    # 余下绘图部分一字不改（figsize/imshow/xticks/.../return mat）
```

- [ ] **Step 4: main() 校验/分支/emit 三处改动**

① extra_mode 校验：

```python
    if extra_mode not in ("hvg", "progeny", "tf"):
        fail("INVALID_INPUT",
             f"extra_mode={extra_mode!r} 非法（需 hvg|progeny|tf）")
        raise SystemExit(1)
```

② extra 分支（progeny if 后插 elif）：

```python
    if extra_mode == "progeny":
        extra = _load_progeny_extra(adata, intra, coords)
        n_pred = int(extra.n_vars)
    elif extra_mode == "tf":
        extra = _load_collectri_extra(adata, intra, coords)
        n_pred = int(extra.n_vars)
    else:
```

③ 热图调用 + emit 键：

```python
    top_n = 30 if extra_mode == "tf" else 0
    para_png = out_dir / "misty_interactions_para.png"
    _para_interactions_heatmap(inter, para_png, top_n=top_n)
```

emit dict 在 `"n_predictors": n_pred,` 后加两键：

```python
        "n_predictors_total": n_pred,
        "n_predictors_shown": min(top_n, n_pred) if top_n else n_pred,
```

模块 docstring 首部 `extra_mode` 说明 `hvg|progeny` 改为 `hvg|progeny|tf` 并补一行 collectri 探针实测摘要（与 progeny 段同款风格）。

- [ ] **Step 5: st.Dockerfile 加快照层**

在 progeny 快照层（`RUN python /tmp/fetch_progeny.py && rm ...`）之后、非 root 用户层之前插：

```dockerfile
# Phase 52 st_misty TF 调控子视图：CollecTRI 模型快照（decoupler Phase 50
# 已装，零 pip 改动；快照需构建期网络，运行期断网读 TSV）
COPY fetch_collectri.py /tmp/fetch_collectri.py
RUN python /tmp/fetch_collectri.py && rm /tmp/fetch_collectri.py
```

- [ ] **Step 6: 重建镜像（快照层实跑，需网络）**

Run: `docker build -t feishu-research-agent/bio:st-cpu-latest sandbox -f sandbox/st.Dockerfile`
Expected: fetch_collectri 输出 `wrote 42xxx rows / 1185 TFs to /opt/collectri/collectri_human.tsv`，构建成功
验证：`docker run --rm --network none feishu-research-agent/bio:st-cpu-latest python -c "import pandas as pd; n=pd.read_csv('/opt/collectri/collectri_human.tsv',sep='\t'); print(n.shape, n['source'].nunique())"`

- [ ] **Step 7: Commit**

```bash
git add sandbox/fetch_collectri.py sandbox/st_tools/misty.py sandbox/st.Dockerfile
git commit -m "feat(st_misty): extra_mode=tf 容器侧（CollecTRI 快照层+MLM 分支+para 热图 top30 截断）"
```

---

### Task 4: 断网容器冒烟——DS_TF 段

**Files:**
- Modify: `scripts/_smoke_st_misty.py`

- [ ] **Step 1: DS_TF 常量 + builder 增 tf 数据集**

`DS_PROG = "stmistyprog"` 行后加：

```python
DS_TF = "stmistytf"
```

`_BUILDER` 尾部（`print("built", n)` 前）加：

```python
# stmistytf：真实 CollecTRI 基因名，DDIT3 靶基因按 weight×tumor 梯度
# 注入（探针 B 同款配方，corr=0.9901 实测可回收）；MLM 秩约束要求
# 交集靶基因 > TF 协变量数——fill 取 4000 个真实靶基因兜底
cnet = pd.read_csv("/opt/collectri/collectri_human.tsv", sep="\t")
dd = cnet[cnet["source"] == "DDIT3"][["target", "weight"]].drop_duplicates(
    "target")
cothers = sorted(set(cnet["target"]) - set(dd["target"]))
cfill = np.random.default_rng(11).choice(cothers, size=4000,
                                         replace=False).tolist()
genes3 = dd["target"].tolist() + cfill
g3idx = pd.Index(genes3)
X3 = np.random.default_rng(1).normal(0, 1, (n, len(genes3))).astype(
    np.float32)
cols3 = g3idx.get_indexer(dd["target"])
X3[:, cols3] += (tumor_frac * 3).astype(np.float32)[:, None] * \
    dd["weight"].to_numpy(dtype=np.float32)[None, :] * 0.5
proc4 = ad.AnnData(X=X3, obs=pd.DataFrame(index=barcodes),
                   var=pd.DataFrame(index=genes3))
proc4.obsm["spatial"] = coords
proc4.write_h5ad("/ws/stmistytf/processed.h5ad")
dec.write_h5ad("/ws/stmistytf/deconv.h5ad")
```

`build_dataset()` 中 `(WS / DS_PROG).mkdir(...)` 行后加：

```python
    (WS / DS_TF).mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 2: 冒烟主流程加 tf 段**

progeny 零交集断言（`no_ov = run_misty(DS, extra_mode="progeny")` 两行）之后加：

```python
# tf 主跑：extra=CollecTRI TF 活性，DDIT3 信号应回收；热图 top30 截断
t = run_misty(DS_TF, extra_mode="tf", bandwidth=2.0)
assert t["ok"], t
assert t["extra_mode"] == "tf", t
assert t["n_predictors_total"] > 500, t
assert t["n_predictors_shown"] == 30, t
with open(WS / DS_TF / "misty/misty_interactions.csv", newline="") as fh:
    trows = list(_csv.DictReader(fh))
tt = [r for r in trows
      if r["view"] == "para" and r["target"] == "Tumor"]
tt.sort(key=lambda r: float(r["importances"]), reverse=True)
ttop30 = {r["predictor"] for r in tt[:30]}
assert "DDIT3" in ttop30, \
    f"DDIT3 not in para top30 for Tumor: {[r['predictor'] for r in tt[:5]]}"

# tf 模式基因名零交集（g0..g59 非 symbol）→ INVALID_INPUT
no_ov_tf = run_misty(DS, extra_mode="tf")
assert not no_ov_tf["ok"] and no_ov_tf["error_code"] == "INVALID_INPUT", \
    no_ov_tf
```

末尾 print 的 `| INVALID_INPUT/...` 段补 `| DDIT3 recovered in tf para top30 (total=%d)" % t["n_predictors_total"]`（格式自行对齐既有风格）。

- [ ] **Step 3: 跑冒烟**

Run: `.venv\Scripts\python.exe scripts\_smoke_st_misty.py`
Expected: `SMOKE OK`（含 DDIT3 回收）。若 DDIT3 未进 top30：csv 回读查其实际排名与 signal 区分度，再定断言松紧（先例：Phase 51 断言降方向性）——实证优先，不预设。

- [ ] **Step 4: Commit**

```bash
git add scripts/_smoke_st_misty.py
git commit -m "test(st_misty): 断网冒烟 tf 段（DDIT3 注入回收+top30 截断+零交集拒绝）"
```

---

### Task 5: 全量门禁 + 文档链 + 推送 + CI

**Files:**
- Modify: `docs/ROADMAP.md`（Phase 51 行后追加 Phase 52 行）
- Modify: `测试总结+2026-09-09T01-55-00.md`（追加"十九"段）

- [ ] **Step 1: 全量回归（门禁口径）**

Run: `.venv\Scripts\python.exe -m pytest -q -m "not pg" 2>&1 | Select-String -Pattern "passed|failed" -Context 0,0`
Expected: **1274 passed**（基线 1272 + 2）

- [ ] **Step 2: ROADMAP 追加 Phase 52 行**

Phase 51 行后追加（单行表格行，风格与 Phase 50/51 行一致）：

```
| 2026-09-12 | **Phase 52 st_misty TF/collectri 调控子视图落地**（spec docs/superpowers/specs/2026-09-12-st-misty-tf-design.md）：Phase 50 非目标留位兑现——st_misty extra_mode 扩第三值 tf（hvg|progeny|tf），CollecTRI TF 活性（decoupler MLM，42990 边/1185 TFs）作 extra 视图，MISTy 三视图建模样式/产物结构一字不改；**断网纪律第三例**：st.Dockerfile 增 fetch_collectri.py 快照 /opt/collectri/collectri_human.tsv（零 pip 改动，decoupler Phase 50 已装）；**探针两轮前置实测**（dc.op.collectri→42990 行 1185 TFs；断网 E2E 注入 DDIT3 100 靶基因 corr=0.9901 回收且高梯度端 top1、区分度 >14×；**MLM 秩约束坑**——overlap=100 时邻接矩阵秩<协变量数断言崩，overlap 阈值定 500）；规模应对=para 热图 top30 截断（772 预测子不可读，csv 全量+emit n_predictors_total/shown 双键）；错误码 ST_MISTY_NO_COLLECTRI/overlap<500 INVALID_INPUT；断网冒烟（DDIT3 weight×tumor 梯度注入回收 para top30、零交集对照复用假名数据集）；TDD 注册 +2 用例（tf 透传/enum）；SECTION_TITLES 38 项不变；全量回归 **1274 passed**（-m 'not pg' 门禁口径，较 Phase 51 同口径 1272 恰 +2） |
```

- [ ] **Step 3: 测试总结追加"十九"段**

文件尾部追加（风格仿十六/十七/十八）：

```markdown
### 十九、Phase 52 st_misty TF/collectri 视图（2026-09-12）

- decoupler MLM 秩约束：net overlap 靶基因数必须 > TF 协变量数
  （探针实证 overlap=100 即 AssertionError），合成数据 fill 必须
  用真实 net 靶基因名且量要足（4000 个），overlap 阈值定 500。
- 构建期快照模式第三例定型（gene_pos→progeny→collectri）：同
  pip 层多个快照脚本各自独立 COPY+RUN 层，改脚本不重装 pip。
- 预测子规模跃迁（14→772）时：csv 全量不截、热图 top-N 截断、
  emit 双键（total/shown）报全量与展示量——展示层截断不丢数据。
- 探针目录（.probe_*）会被 ruff 全仓扫描拦截，探针完成后立即删
  除（结果进 spec §2 即完成使命），勿留进 pre-push。
```

- [ ] **Step 4: 推送（pre-push 四道门自动跑）+ CI 轮询**

```bash
git add docs/ROADMAP.md "测试总结+2026-09-09T01-55-00.md"
git commit -m "docs: Phase 52 ROADMAP/测试总结收官"
git push
```

CI 轮询：`git credential fill` 取 PAT → GitHub API `/actions/runs?per_page=1` 查最新 run conclusion=success（先例流程，gh CLI 不可用）。

- [ ] **Step 5: 冒烟数据集清理**

`bio_workspace/stmistysmoke/stmistynodec/stmistyprog/stmistytf/_builder_stmisty` 可留可清（先例：留作调试资产，不占仓库）。

---

## Self-Review

**1. Spec coverage：**
- §2 快照层 → Task 3 Step 1/5/6 ✓
- §3 数据流/防御（overlap 500、NO_COLLECTRI）→ Task 3 Step 2 ✓
- §4 参数（enum 三值）→ Task 2 ✓
- §5 top30 截断 + emit 双键 → Task 3 Step 3/4③ ✓
- §6 错误码 → Task 3 Step 2/4①（NO_COLLECTRI 不覆盖冒烟，spec §7 已注明策略）✓
- §7 测试（+2 注册、冒烟 DS_TF、1274 口径）→ Task 1/4/5 ✓
- 非目标无对应任务（正确）✓

**2. Placeholder scan：** Task 3 Step 3 有"余下绘图部分一字不改"——该函数绘图体在 misty.py L152-162 现存不变，属编辑定位说明而非占位（绘图代码已在现文件，非待写）；Task 4 Step 3 的失败处置给了明确动作非 TBD。其余步骤全含完整代码/命令。

**3. Type consistency：** `_load_collectri_extra(adata: Any, intra: Any, coords: np.ndarray) -> Any` 与 `_load_progeny_extra` 签名一致；`_para_interactions_heatmap(inter, png_path, top_n=0)` 调用处 `top_n=top_n` 一致；emit 键 `n_predictors_total/shown` Task 3 定义、Task 4 断言同名；`COLLECTRI_TSV`/`DS_TF`/`ST_MISTY_NO_COLLECTRI` 全程一致。
