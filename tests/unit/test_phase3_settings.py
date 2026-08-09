from config.settings import Settings


def test_phase3_settings_defaults():
    s = Settings.__dataclass_fields__
    assert "context_compress_trigger_ratio" in s
    assert "context_freeze_trigger_ratio" in s
    assert "loop_max_iterations_default_while" in s
    assert "loop_max_iterations_default_for" in s
    assert "bind_doc_renew_threshold_sec" in s