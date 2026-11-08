from jaxon.core.config import Settings


def test_csv_and_json_list_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_WEB_DOMAINS", "example.com, acme.io")
    monkeypatch.setenv("CORS_ORIGINS", '["http://a.test","http://b.test"]')
    s = Settings(_env_file=None)
    assert s.allowed_web_domains == ["example.com", "acme.io"]
    assert s.cors_origins == ["http://a.test", "http://b.test"]


def test_secrets_are_masked(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    s = Settings(_env_file=None)
    assert "sk-test-not-real" not in repr(s)
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "sk-test-not-real"


def test_defaults_have_no_credentials(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "JAXON_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.anthropic_api_key is None and s.github_token is None and s.jaxon_api_token is None
    assert s.default_model == "claude-opus-5-5"
    assert s.sync_database_url().startswith("postgresql+psycopg://")
