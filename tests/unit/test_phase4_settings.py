from config.settings import Settings


def test_phase4_blast_settings_exist():
    s = Settings.__dataclass_fields__
    assert "blast_api_base_url" in s
    assert "blast_rate_per_sec" in s
    assert "blast_timeout_sec" in s
    assert "blast_max_hits_default" in s
