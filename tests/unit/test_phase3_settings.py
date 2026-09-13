from config.settings import Settings


def test_load_yaml_forces_utf8(tmp_path, monkeypatch):
    """_load_yaml 强制 UTF-8：GBK locale 环境（explorer 自启无 PYTHONUTF8）不炸。

    2026-09-13 事故：llm.yaml 含 UTF-8 中文，open() 缺省 encoding 走 locale
    （ explorer 自启环境 GBK）→ UnicodeDecodeError → ws_client 启动期
    静默死亡两天（traceback 随 CREATE_NO_WINDOW pythonw 丢失）。
    """
    import builtins

    from config import settings as settings_mod

    y = tmp_path / "x.yaml"
    y.write_text("name: 中文注释\n", encoding="utf-8")
    seen: dict[str, object] = {}
    real_open = builtins.open

    def spy(file, mode="r", *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        seen["encoding"] = kwargs.get("encoding")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    assert settings_mod._load_yaml(str(y)) == {"name": "中文注释"}
    assert seen["encoding"] == "utf-8"


def test_phase3_settings_defaults():
    s = Settings.__dataclass_fields__
    assert "context_compress_trigger_ratio" in s
    assert "context_freeze_trigger_ratio" in s
    assert "loop_max_iterations_default_while" in s
    assert "loop_max_iterations_default_for" in s
    assert "bind_doc_renew_threshold_sec" in s
