from orchestrator.tools.ast_guard import ASTGuard


def test_returns_ast_report_for_safe_code():
    guard = ASTGuard()
    report = guard.check("x = 1 + 1")
    assert report.blocked is False
    assert report.notices == []


def test_p0_still_blocks():
    from shared.errors import ToolBlockedError
    guard = ASTGuard()
    try:
        guard.check("import os\nos.system('x')")
        assert False
    except ToolBlockedError:
        pass


def test_p1_notice_on_requests():
    guard = ASTGuard()
    report = guard.check("import requests\nrequests.get('http://x')")
    assert report.blocked is False
    assert any(n[0] == "P1" for n in report.notices)


def test_p2_notice_on_open_outside_workspace():
    guard = ASTGuard()
    report = guard.check("open('/etc/passwd')")
    assert report.blocked is False
    assert any(n[0] == "P2" for n in report.notices)


def test_p1_notice_on_urllib():
    guard = ASTGuard()
    report = guard.check("from urllib.request import urlopen\nurlopen('http://x')")
    assert any(n[0] == "P1" for n in report.notices)


def test_p1_notice_on_httpx():
    guard = ASTGuard()
    report = guard.check("import httpx\nhttpx.get('http://x')")
    assert any(n[0] == "P1" for n in report.notices)
