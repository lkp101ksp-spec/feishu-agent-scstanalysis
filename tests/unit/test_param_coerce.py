"""Phase 24：param_coerce.coerce_params 单测（spec §4）。"""
from orchestrator.tools.param_coerce import coerce_params

SCHEMA = {
    "type": "object",
    "properties": {
        "genes": {"type": "array"},
        "blocks": {"type": "object"},
        "name": {"type": "string"},
        "count": {"type": "integer"},
    },
}


def test_array_single_quote_repr():
    """单引号 repr 串（planner str() 强转典型产物）→ list。"""
    fixed, names = coerce_params(SCHEMA, {"genes": "['A', 'B']"})
    assert fixed["genes"] == ["A", "B"]
    assert names == ["genes"]


def test_array_valid_json():
    """合法 JSON 串同样纠正。"""
    fixed, names = coerce_params(SCHEMA, {"genes": '["X"]'})
    assert fixed["genes"] == ["X"]
    assert names == ["genes"]


def test_object_repr():
    fixed, names = coerce_params(SCHEMA, {"blocks": "{'a': 1}"})
    assert fixed["blocks"] == {"a": 1}
    assert names == ["blocks"]


def test_unparseable_kept():
    """无法解析的 str 原样保留（交给下游校验报错，不掩盖真错误）。"""
    fixed, names = coerce_params(SCHEMA, {"genes": "not a list"})
    assert fixed["genes"] == "not a list"
    assert names == []


def test_type_mismatch_kept():
    """声明 array 但解析出 dict → 原样保留。"""
    fixed, names = coerce_params(SCHEMA, {"genes": "{'a': 1}"})
    assert fixed["genes"] == "{'a': 1}"
    assert names == []


def test_non_coercible_types_untouched():
    """string/integer 声明不动（YAGNI：真机未见数字串翻车）。"""
    fixed, names = coerce_params(SCHEMA, {"name": "'hi'", "count": "5"})
    assert fixed["name"] == "'hi'" and fixed["count"] == "5"
    assert names == []


def test_real_types_untouched():
    """已是真 list/dict 幂等不动（与既有点修共存）。"""
    fixed, names = coerce_params(SCHEMA, {"genes": ["A"], "blocks": {"a": 1}})
    assert fixed["genes"] == ["A"] and fixed["blocks"] == {"a": 1}
    assert names == []


def test_empty_string_untouched():
    fixed, names = coerce_params(SCHEMA, {"genes": "   "})
    assert fixed["genes"] == "   "
    assert names == []


def test_schema_without_properties():
    """schema 缺 properties 键 → 原样返回不炸。"""
    fixed, names = coerce_params({"type": "object"}, {"genes": "['A']"})
    assert fixed["genes"] == "['A']"
    assert names == []
