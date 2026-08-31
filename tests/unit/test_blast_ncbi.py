import json

import httpx
import pytest
import respx

from orchestrator.tools.bio.blast_ncbi import BlastNCBITool

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _esearch_json(ids, total_count):
    return {
        "esearchresult": {
            "idlist": ids,
            "count": str(total_count),
        }
    }


def _efetch_json(records):
    result = {"uids": [r["id"] for r in records]}
    for r in records:
        result[r["id"]] = {
            "uid": r["id"],
            "title": r["title"],
            "summary": r["summary"],
            "length": r.get("length", 0),
        }
    return {"result": result}


def _fasta_text(pairs):
    """构造 fasta 文本：pairs = [(id, seq), ...]。"""
    return "".join(f">{uid} title\n{seq}\n" for uid, seq in pairs)


def _mock_fasta(pairs):
    """mock efetch fasta 路由（长度回填用），与 docsum 路由并存分流。"""
    respx.get(f"{_BASE}/efetch.fcgi", params__contains={"rettype": "fasta"}).mock(
        return_value=httpx.Response(200, text=_fasta_text(pairs))
    )


class NoWaitLimiter:
    def wait(self):
        pass


@respx.mock
def test_handle_efetch_concatenated_json_merges_records(blast):
    """大响应多段 JSON 拼接：逐段解析合并 records（真机 2026-08-31）。"""
    respx.post(f"{_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_esearch_json(["111", "222"], 2))
    )
    # 两段完整 JSON 无分隔符拼接（NCBI 大响应实测行为）
    body = json.dumps(_efetch_json([
        {"id": "111", "title": "BRCA1", "summary": "s1", "length": 100},
    ])) + json.dumps(_efetch_json([
        {"id": "222", "title": "BRCA2", "summary": "s2", "length": 200},
    ]))
    respx.get(f"{_BASE}/efetch.fcgi").mock(
        return_value=httpx.Response(200, text=body)
    )
    out = blast.handle(query="BRCA1", database="nr", max_hits=2)
    assert out["ids"] == ["111", "222"]
    assert [r["title"] for r in out["records"]] == ["BRCA1", "BRCA2"]


@respx.mock
def test_handle_fills_real_lengths_from_fasta(blast):
    """fasta 回填真实序列长度；docsum length=0 被覆盖（真机 2026-08-31 全 0）。"""
    respx.post(f"{_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_esearch_json(["111", "222"], 2))
    )
    respx.get(f"{_BASE}/efetch.fcgi", params__contains={"rettype": "docsum"}).mock(
        return_value=httpx.Response(200, json=_efetch_json([
            {"id": "111", "title": "BRCA1", "summary": "s1", "length": 0},
            {"id": "222", "title": "BRCA2", "summary": "s2", "length": 0},
        ]))
    )
    # fasta：111 序列 6 aa（含跨行），222 序列 2 aa
    _mock_fasta([("111", "MAV\nQVQ"), ("222", "MK")])
    out = blast.handle(query="BRCA1", database="nr", max_hits=2)
    assert {r["id"]: r["length"] for r in out["records"]} == {"111": 6, "222": 2}


@respx.mock
def test_handle_fasta_failure_degrades_to_zero(blast):
    """fasta 调用失败降级：length 保留 docsum 值，检索不炸。"""
    respx.post(f"{_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_esearch_json(["111"], 1))
    )
    respx.get(f"{_BASE}/efetch.fcgi", params__contains={"rettype": "docsum"}).mock(
        return_value=httpx.Response(200, json=_efetch_json([
            {"id": "111", "title": "BRCA1", "summary": "s", "length": 0},
        ]))
    )
    respx.get(f"{_BASE}/efetch.fcgi", params__contains={"rettype": "fasta"}).mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    out = blast.handle(query="BRCA1", database="nr", max_hits=1)
    assert out["records"][0]["length"] == 0  # 降级保留 0，无 error_code


@pytest.fixture
def blast():
    return BlastNCBITool(
        rate_limiter=NoWaitLimiter(),
        timeout_sec=5,
    )


@respx.mock
def test_handle_returns_ids_and_records(blast):
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_esearch_json(["111", "222"], 2))
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(
            200,
            json=_efetch_json([
                {"id": "111", "title": "BRCA1", "summary": "DNA repair", "length": 100},
                {"id": "222", "title": "BRCA2", "summary": "DNA repair", "length": 200},
            ]),
        )
    )
    out = blast.handle(query="BRCA1", database="nr", max_hits=2)
    assert out["ids"] == ["111", "222"]
    assert len(out["records"]) == 2
    assert out["records"][0]["title"] == "BRCA1"
    assert out["total_count"] == 2
    assert out["query"] == "BRCA1"
    # BLAST 库名 nr 映射为 Entrez 库 protein（真机 2026-08-30：
    # db=nr esearch 静默 0 命中）
    assert out["database"] == "protein"


@respx.mock
def test_handle_maps_blast_db_names_to_entrez(blast):
    """nr→protein、nt→nucleotide；真实 Entrez 库名原样透传。"""
    route = respx.post(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    ).mock(return_value=httpx.Response(200, json=_esearch_json([], 0)))
    blast.handle(query="BRCA1", database="nt", max_hits=5)
    assert route.calls.last.request.content == (
        b"db=nucleotide&term=BRCA1&retmax=5&retmode=json"
    )
    out = blast.handle(query="BRCA1", database="pubmed", max_hits=5)
    assert out["database"] == "pubmed"  # 非别名原样透传


@respx.mock
def test_handle_returns_empty_when_no_hits(blast):
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_esearch_json([], 0))
    )
    out = blast.handle(query="XXXX", database="nr", max_hits=5)
    assert out["ids"] == []
    assert out["records"] == []
    assert out["total_count"] == 0


def test_handle_invalid_query_returns_error(blast):
    out = blast.handle(query="", database="nr", max_hits=5)
    assert out["error_code"] == "BLAST_INVALID_QUERY"


@respx.mock
def test_handle_esearch_error_returns_error_code(blast):
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    out = blast.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["error_code"] == "BLAST_API_ERROR"


@respx.mock
def test_handle_efetch_error_returns_error_code(blast):
    respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=_esearch_json(["111"], 1))
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    out = blast.handle(query="BRCA1", database="nr", max_hits=5)
    assert out["error_code"] == "BLAST_API_ERROR"
