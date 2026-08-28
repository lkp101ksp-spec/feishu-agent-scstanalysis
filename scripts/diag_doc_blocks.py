"""一次性诊断：列出文档根块，定位锚点块与新写入块的实际位置。"""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

# 读 .env
env = {}
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()

tenant = env["FEISHU_TENANT_TOKEN"] if "FEISHU_TENANT_TOKEN" in env else None
# 用 tenant_access_token 最简单
app_id, secret = env["FEISHU_APP_ID"], env["FEISHU_APP_SECRET"]
r = httpx.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
               json={"app_id": app_id, "app_secret": secret}, timeout=15)
token = r.json()["tenant_access_token"]
print(f"token ok: {token[:8]}***")

DOC = sys.argv[1] if len(sys.argv) > 1 else "L9AXd9xmdoZuSgx75mccKB82nkb"
headers = {"Authorization": f"Bearer {token}"}

# wiki: 前缀 → 实时经 get_node 解析（与生产 BindDocService 同一链路）
if DOC.startswith("wiki:"):
    node = httpx.get("https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node",
                     headers=headers, params={"token": DOC[5:], "obj_type": "wiki"},
                     timeout=15).json()
    n = node.get("data", {}).get("node", {})
    print(f"wiki 解析: obj_type={n.get('obj_type')} obj_token={n.get('obj_token')} title={n.get('title')!r}")
    DOC = n["obj_token"]
print(f"读取文档: {DOC}\n")

blocks, page = [], None
while True:
    params = {"document_revision_id": -1, "page_size": 500}
    if page:
        params["page_token"] = page
    resp = httpx.get(f"https://open.feishu.cn/open-apis/docx/v1/documents/{DOC}/blocks/{DOC}/children",
                     headers=headers, params=params, timeout=30).json()
    data = resp.get("data", {})
    blocks.extend(data.get("items", []))
    page = data.get("page_token")
    if not data.get("has_more"):
        break
print(f"root children: {len(blocks)}\n")

def text_of(b):
    """提取块文字（text/heading/bullet 通用：找带 elements 的子 dict）。"""
    for v in b.values():
        if isinstance(v, dict) and "elements" in v:
            out = []
            for e in v["elements"]:
                if isinstance(e, dict) and "text_run" in e:
                    out.append(e["text_run"].get("content", ""))
            return "".join(out)
    return ""

for i, b in enumerate(blocks):
    t = text_of(b)
    mark = ""
    if "测试" in t:
        mark = "  <<< 含'测试'"
    print(f"[{i:3d}] {b.get('block_id','')[:26]} type={b.get('block_type'):>2} {t[:45]!r}{mark}")
