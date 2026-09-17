"""bio_trajectory_pipeline 编排脚本（Phase 60 建议③）。

按 Phase 53-59 最佳实践串五步（docker 调 bio 镜像内 sc_tools，
与 L3 同一份分析代码）：
  1. sc_pseudotime trajectory_full（全景：palantir 分支+slingshot 谱系+cross+paga）
  2. sc_meta list_cols（分组/轨迹列发现）
  3. sc_plot umap_obs（lineage_branch / slingshot_lineage 着色）
  4. sc_cellfreq celltype_col=lineage_branch（group 列存在时）
  5. steps=full 时 sc_cellchat celltype_col=lineage_branch

输入 --k v（skill_loader 展开布尔为旗标，本工具均为字符串参数）。
输出 bio_workspace/<ds>/trajectory_pipeline_summary.json。
"""

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

# 执行面统一：镜像/工作区/容器资源与 settings 同 env 口径（BIO_IMAGE/
# BIO_WORKSPACE_ROOT/BIO_CPUS/BIO_MEMORY），不再硬编码 tag 与 4c/16g——
# settings 换配置时 skill 不漂移
WS_ROOT = Path(os.environ.get(
    "BIO_WORKSPACE_ROOT", "I:/飞书agent/bio_workspace"))
IMAGE = os.environ.get("BIO_IMAGE", "feishu-research-agent/bio:cpu-latest")
CPUS = os.environ.get("BIO_CPUS", "4")
MEMORY = os.environ.get("BIO_MEMORY", "16g")
# 每步超时（秒）：skill 编排五步串行全景，单步成本高于 L3 单工具档
# （1200s 级）——trajectory_full 含 enrich+PAGA 历史峰值 30min+，3900s
# 留 25% 余量。口径表：docs/superpowers/specs/
# 2026-09-17-execution-plane-unification-design.md 附录 A（测试守护）。
STEP_TIMEOUT_SEC = 3900
TOOLS = {
    "pseudotime": "/opt/sc_tools/pseudotime.py",
    "meta": "/opt/sc_tools/meta.py",
    "plot": "/opt/sc_tools/plot.py",
    "cellfreq": "/opt/sc_tools/cellfreq.py",
    "cellchat": "/opt/sc_tools/cellchat.py",
}


def run_tool(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    """docker 跑 sc_tools 单步，返回 emit 末行 JSON；失败抛 RuntimeError。

    --name + 超时 docker kill：对齐 BioRunner 僵尸容器治理（subprocess
    超时只杀 docker CLI，容器会残留抢 CPU）。
    """
    cname = f"bio-skill-{uuid.uuid4().hex[:12]}"
    try:
        proc = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                "--name",
                cname,
                "--network",
                "none",
                "--cpus",
                CPUS,
                "--memory",
                MEMORY,
                "-v",
                f"{WS_ROOT}:/ws",
                IMAGE,
                "python",
                TOOLS[tool],
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=STEP_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", cname], capture_output=True)
        raise RuntimeError(
            f"{tool} 超时（{STEP_TIMEOUT_SEC}s），容器 {cname} 已 kill") from None
    if proc.returncode != 0:
        raise RuntimeError(f"{tool} 失败（exit {proc.returncode}）：{proc.stderr[-500:]}")
    line = next(
        (ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), "")
    if not line:
        raise RuntimeError(f"{tool} 无 emit 输出")
    out: dict[str, Any] = json.loads(line)
    if out.get("ok") is False:
        raise RuntimeError(f"{tool} 返回错误 {out.get('error_code')}：{out.get('error_message')}")
    return out


def main() -> int:
    args: dict[str, str] = {
        k.lstrip("-"): v for k, v in zip(sys.argv[1::2], sys.argv[2::2])}
    ds = args.get("dataset_id")
    root = args.get("root_cluster")
    if not ds or not root:
        print(
            "错误：dataset_id 与 root_cluster 必填。root_cluster 应为祖/干细胞簇"
            "（leiden 簇号）；未知时请先按 marker 推断并向用户确认，勿留空。"
        )
        return 2
    species = args.get("species", "human")
    steps = args.get("steps", "fast")
    summary: dict[str, Any] = {
        "dataset_id": ds,
        "root_cluster": root,
        "species": species,
        "steps": steps,
        "pipeline": [],
    }

    # 断点续跑（2026-09-16 建议3）：上轮 summary 中 ok 的步骤直接复用
    # （产物路径不变），失败重试只补跑缺口——30 分钟级任务重试成本减半；
    # fast→full 升级时 cellchat 不在旧清单照常补跑。--fresh 1 强制全重跑。
    out_path = WS_ROOT / ds / "trajectory_pipeline_summary.json"
    prev_steps: dict[str, dict[str, Any]] = {}
    if out_path.exists() and args.get("fresh", "0") != "1":
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            prev_steps = {s.get("step", ""): s
                          for s in prev.get("pipeline", []) if s.get("ok")}
        except Exception:  # noqa: BLE001 —— 旧 summary 损坏则全重跑
            prev_steps = {}
    resumed: list[str] = []

    def _step(name: str,
              run_fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """上轮 ok 步骤复用（产物未变），否则执行 run_fn。"""
        if name in prev_steps:
            resumed.append(name)
            return dict(prev_steps[name])
        return run_fn()

    # 1) 轨迹全景（Phase 59 一键模式）
    def _run_traj() -> dict[str, Any]:
        out = run_tool(
            "pseudotime",
            {
                "dataset_id": ds,
                "trajectory_full": True,
                "root_cluster": root,
                "dyn_modules_k": 6,
                "modules_enrich": "go_bp",
                "paga": True,
                "paga_pt": True,
            },
        )
        pal, sling = out.get("palantir", {}), out.get("slingshot", {})
        return {
            "step": "trajectory_full",
            "ok": out.get("ok", True),
            "n_terminal": pal.get("n_terminal"),
            "n_lineages": sling.get("n_lineages"),
            "n_cross_sig": sling.get("n_cross_sig"),
            "cross_triggered_by": sling.get("cross_triggered_by"),
            "paga_edges": pal.get("n_paga_edges"),
            "branch_counts": pal.get("branch_counts"),
            "artifacts": {k: v for k, v in {**pal, **sling}.items() if k.endswith(("_csv", "_png"))},
        }

    summary["pipeline"].append(_step("trajectory_full", _run_traj))

    # 2) 列发现（只读）
    cols = run_tool("meta", {"dataset_id": ds, "op": "list_cols"})
    detail = cols.get("detail", {})
    group_cols = [g.get("col") for g in detail.get("group_cols", [])]
    summary["group_cols_found"] = group_cols
    summary["trajectory_cols_found"] = detail.get("trajectory_cols", [])

    # 3) 谱系/命运支 UMAP 着色（列存在才画）
    obs_cols = [
        c
        for c in ("lineage_branch", "slingshot_lineage")
        if c in (detail.get("trajectory_cols") or []) or c in group_cols
    ]
    if obs_cols:

        def _run_plot() -> dict[str, Any]:
            plot = run_tool("plot", {"dataset_id": ds, "kind": "umap_obs", "obs_cols": obs_cols})
            return {"step": "umap_obs", "ok": plot.get("ok", True), "pngs": plot.get("pngs", [])}

        summary["pipeline"].append(_step("umap_obs", _run_plot))

    # 4) 命运偏向（存在 group 列时）
    group = args.get("group_col") or ("group" if "group" in group_cols else None)
    if group:

        def _run_cf() -> dict[str, Any]:
            cf = run_tool(
                "cellfreq",
                {"dataset_id": ds, "by": "sample", "group": group,
                 "celltype_col": "lineage_branch"},
            )
            return {
                "step": "cellfreq",
                "ok": cf.get("ok", True),
                "fate_bias": cf.get("fate_bias"),
                "roe_csv": cf.get("roe_csv"),
            }

        summary["pipeline"].append(_step("cellfreq", _run_cf))

    # 5) 支间通讯（full）
    if steps == "full":

        def _run_cc() -> dict[str, Any]:
            cc = run_tool(
                "cellchat",
                {"dataset_id": ds, "celltype_col": "lineage_branch",
                 "species": species},
            )
            return {
                "step": "cellchat",
                "ok": cc.get("ok", True),
                "n_pairs": cc.get("n_pairs_tested"),
                "n_sig": cc.get("n_sig"),
                "top": cc.get("top", [])[:5],
                "csv": cc.get("csv"),
            }

        summary["pipeline"].append(_step("cellchat", _run_cc))

    if resumed:
        summary["resumed_steps"] = resumed
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "summary_json": str(out_path),
                "steps_done": [s["step"] for s in summary["pipeline"]],
                "resumed": resumed,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
