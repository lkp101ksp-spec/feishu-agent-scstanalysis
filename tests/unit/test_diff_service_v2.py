"""Phase 9 T2: diff v2 单元测试（hash 配对 + moved，ADR-0027）。"""
import json
from unittest.mock import MagicMock

from orchestrator.templates.diff_service import VersionDiffService, _diff_lists


def _ver(blocks=None, steps=None, name="std", desc="d"):
    v = MagicMock()
    v.blocks_json = json.dumps(blocks) if blocks else None
    v.steps_json = json.dumps(steps) if steps else None
    v.name = name
    v.description = desc
    return v


def _svc(va, vb):
    repo = MagicMock()
    repo.get_by_version.side_effect = lambda tid, vn: (
        va if vn == 1 else vb if vn == 2 else None
    )
    return VersionDiffService(repo)


def test_moved_detected_on_reorder():
    out = _diff_lists(
        [{"type": "heading", "text": "A"}, {"type": "code", "language": "py"}],
        [{"type": "code", "language": "py"}, {"type": "heading", "text": "A"}],
        key="type",
    )
    assert len(out["moved"]) == 2  # 两块都换了位置
    assert out["changed"] == []
    assert out["added"] == []
    assert out["removed"] == []


def test_identical_same_position_no_output():
    out = _diff_lists(
        [{"type": "heading", "text": "A"}, {"type": "code", "language": "py"}],
        [{"type": "heading", "text": "A"}, {"type": "code", "language": "py"}],
        key="type",
    )
    for section in ("added", "removed", "changed", "moved"):
        assert out[section] == []


def test_modified_falls_to_changed():
    out = _diff_lists(
        [{"type": "heading", "text": "A"}],
        [{"type": "heading", "text": "B"}],
        key="type",
    )
    assert out["changed"][0]["fields"] == ["text"]
    assert out["moved"] == []


def test_mixed_added_removed():
    out = _diff_lists(
        [{"type": "heading", "text": "A"}, {"type": "quote", "text": "q"}],
        [{"type": "heading", "text": "A"}, {"type": "code", "language": "py"}],
        key="type",
    )
    # heading 位置不变 → unchanged；quote→code 是同组吗？不同 type 组：
    # quote 组 a 有 b 无 → removed；code 组 b 有 a 无 → added
    assert [r["type"] for r in out["removed"]] == ["quote"]
    assert [a["type"] for a in out["added"]] == ["code"]


def test_service_render_moved_symbol():
    svc = _svc(
        _ver(blocks=[{"type": "heading", "text": "A"},
                     {"type": "code", "language": "py"}]),
        _ver(blocks=[{"type": "code", "language": "py"},
                     {"type": "heading", "text": "A"}]),
    )
    diff = svc.diff(template_id="t1", v_a=1, v_b=2)
    text = svc.render(diff)
    assert "↔ [0→1] heading" in text
    assert "↔ [1→0] code" in text
    assert "（无差异）" not in text


def test_service_same_versions_still_no_diff_placeholder():
    svc = _svc(
        _ver(blocks=[{"type": "heading", "text": "A"}]),
        _ver(blocks=[{"type": "heading", "text": "A"}]),
    )
    diff = svc.diff(template_id="t1", v_a=1, v_b=2)
    assert "（无差异）" in svc.render(diff)
