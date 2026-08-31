"""诊断 NCBI efetch 大响应：不同 max_hits 下响应大小与 JSON 合法性。

复现 blast_search 真机失败（2026-08-31：Extra data: char 99176）。
"""
import json

import httpx

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def fetch(max_hits: int, term: str = "BRCA1 human", db: str = "protein") -> None:
    """esearch 取 max_hits 条 id → efetch docsum → 验证 JSON 合法性。"""
    with httpx.Client(timeout=60) as client:
        r = client.post(
            f"{BASE}/esearch.fcgi",
            data={"db": db, "term": term, "retmax": str(max_hits), "retmode": "json"},
        )
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        print(f"max_hits={max_hits}: esearch 取到 {len(ids)} 条 id")
        if not ids:
            return
        resp = client.get(
            f"{BASE}/efetch.fcgi",
            params={"db": db, "id": ",".join(ids), "rettype": "docsum", "retmode": "json"},
        )
        resp.raise_for_status()
        raw = resp.text
        print(f"  efetch body {len(raw)} chars", end="")
        try:
            json.loads(raw)
            print("  JSON OK")
        except json.JSONDecodeError as e:
            lo, hi = max(0, e.pos - 150), min(len(raw), e.pos + 150)
            print(f"  JSON FAIL: {e}")
            print(f"  出错位置前后: ...{raw[lo:hi]!r}...")


def main() -> None:
    """按不同 max_hits 梯度复现，找到触发 Extra data 的规模。"""
    for hits in (5, 20, 50, 100, 200):
        fetch(hits)


if __name__ == "__main__":
    main()
