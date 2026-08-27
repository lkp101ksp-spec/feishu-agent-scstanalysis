"""E1-E3: BLAST 工具端到端（mock API + Planner + Executor）。"""
import time

import httpx
import respx

from orchestrator.tools.bio.rate_limiter import RateLimiter
from orchestrator.tools.builtin.l3_bio import register_l3_bio
from orchestrator.tools.tool_handler import ToolHandler
from orchestrator.tools.tool_registry import ToolRegistry


def _esearch_json(ids, total):
    return {"esearchresult": {"idlist": ids, "count": str(total)}}


def _efetch_json(records):
    result = {"uids": [r["id"] for r in records]}
    for r in records:
        result[r["id"]] = {
            "uid": r["id"], "title": r["title"],
            "summary": r["summary"], "length": r.get("length", 0),
        }
    return {"result": result}


@respx.mock
def test_e1_blast_end_to_end_with_mock():
    """E1: blast BRCA1 → ToolRegistry 路由 → BLAST 工具 → ToolResult 含 hits。"""
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_esearch_json(["111", "222"], 2))
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(200, json=_efetch_json([
            {"id": "111", "title": "BRCA1", "summary": "DNA repair", "length": 100},
            {"id": "222", "title": "BRCA2", "summary": "DNA repair", "length": 200},
        ]))
    )

    reg = ToolRegistry()
    register_l3_bio(reg)
    handler = ToolHandler(registry=reg)

    result = handler.execute(
        "blast_search",
        {"query": "BRCA1", "database": "nr", "max_hits": 2},
        actor_open_id="ou_1", session_id="s1",
    )
    assert result.error_code is None
    assert result.outputs["ids"] == ["111", "222"]
    assert len(result.outputs["records"]) == 2


def test_e2_blast_rate_limiter_enforces_interval():
    """E2: RateLimiter 在 3 req/s 时强制间隔 ~0.333s。"""
    rl = RateLimiter(rate=3, per_sec=1.0)
    rl.wait()
    t0 = time.monotonic()
    rl.wait()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.30


@respx.mock
def test_e3_blast_api_error_returns_error_code():
    """E3: NCBI 返回 500 → ToolResult.error_code="BLAST_API_ERROR"。"""
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )

    reg = ToolRegistry()
    register_l3_bio(reg)
    handler = ToolHandler(registry=reg)

    result = handler.execute(
        "blast_search",
        {"query": "BRCA1", "database": "nr", "max_hits": 5},
        actor_open_id="ou_1", session_id="s1",
    )
    assert result.error_code == "BLAST_API_ERROR"
