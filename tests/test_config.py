from toadies import config


def test_toadette_graduation_env_toggle(monkeypatch):
    monkeypatch.delenv("TOADIES_GRADUATED_TOADIES", raising=False)
    monkeypatch.delenv("TOADIES_TOADETTE_GRADUATED", raising=False)
    assert config.is_toadie_graduated("toadette") is False
    assert config.is_toadie_graduated("scout") is False

    monkeypatch.setenv("TOADIES_TOADETTE_GRADUATED", "yes")
    assert config.is_toadie_graduated("toadette") is True


def test_graduated_toadies_supports_comma_list(monkeypatch):
    monkeypatch.setenv("TOADIES_GRADUATED_TOADIES", "toadette,Scout")
    assert config.is_toadie_graduated("toadette") is True
    assert config.is_toadie_graduated("scout") is True
