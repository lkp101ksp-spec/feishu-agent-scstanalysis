"""拉取最近一次 CI job 日志：段错误上下文或失败摘要（PAT 从 GCM 读，不走命令行）。"""
import subprocess

import httpx

REPO = "lkp101ksp-spec/feishu-agent"
API = f"https://api.github.com/repos/{REPO}/actions"


def get_token() -> str:
    """从 git credential-manager 缓存读 GitHub PAT。"""
    p = subprocess.run(
        ["git", "credential-manager", "get"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, timeout=20,
    )
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("no cached credential")


def main() -> None:
    """打印最近运行状态；失败时给段错误上下文或 FAILED 列表+日志尾部。"""
    h = {"Authorization": f"Bearer {get_token()}",
         "Accept": "application/vnd.github+json"}
    with httpx.Client(proxy="http://127.0.0.1:17890", timeout=30,
                      headers=h, follow_redirects=True) as c:
        runs = c.get(f"{API}/runs", params={"per_page": 1}).json()
        run = runs["workflow_runs"][0]
        print(f"run {run['id']}: {run['status']} / {run['conclusion']} "
              f"({run['head_sha'][:7]})")
        jobs = c.get(f"{API}/runs/{run['id']}/jobs").json()["jobs"]
        job = next((j for j in jobs if j["conclusion"] == "failure"), None)
        if job is None:
            for j in jobs:
                print(f"  job {j['name']}: {j['status']} / {j['conclusion']}")
            return
        log = c.get(f"{API}/jobs/{job['id']}/logs").text
    lines = log.splitlines()
    seg = [i for i, line in enumerate(lines) if "Segmentation fault" in line]
    if seg:
        idx = seg[0]
        lo, hi = max(0, idx - 40), min(len(lines), idx + 30)
        for i in range(lo, hi):
            print(f"{i:5d}| {lines[i][:160]}")
        return
    for line in lines:
        if " FAILED " in line or " ERROR " in line:
            print(line[25:200])
    import sys
    if len(sys.argv) > 1:
        needle = sys.argv[1]
        hits = [i for i, line in enumerate(lines)
                if f"_ {needle} _" in line or f"_{needle}_" in line]
        if hits:
            lo = hits[0]
            for i in range(lo, min(len(lines), lo + 160)):
                print(f"{i:5d}| {lines[i][:200]}")


if __name__ == "__main__":
    main()
