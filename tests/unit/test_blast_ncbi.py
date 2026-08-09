import httpx
import pytest
import respx

from orchestrator.tools.bio.blast_ncbi import BlastNCBITool


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


class NoWaitLimiter:
    def wait(self):
        pass


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
    assert out["database"] == "nr"


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