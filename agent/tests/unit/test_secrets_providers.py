import pytest
from autosentry_agent.secrets.env_provider import EnvSecretsProvider
from autosentry_agent.config import get_secrets_provider, SecretsBackendError

def test_env_provider_reads_value(monkeypatch):
    monkeypatch.setenv("TG_TEST_SECRET", "hunter2")
    provider = EnvSecretsProvider()
    assert provider.get_secret("TG_TEST_SECRET") == "hunter2"

def test_env_provider_missing_secret_raises(monkeypatch):
    monkeypatch.delenv("TG_MISSING_SECRET", raising=False)
    provider = EnvSecretsProvider()
    with pytest.raises(KeyError):
        provider.get_secret("TG_MISSING_SECRET")

def test_env_provider_version_is_constant():
    provider = EnvSecretsProvider()
    assert provider.get_secret_version("anything") is None

def test_factory_fails_closed_on_unknown_backend(monkeypatch):
    monkeypatch.setenv("SECRETS_BACKEND", "not-a-real-backend")
    with pytest.raises(SecretsBackendError):
        get_secrets_provider()

def test_factory_returns_env_provider_by_default(monkeypatch):
    monkeypatch.setenv("SECRETS_BACKEND", "env")
    provider = get_secrets_provider()
    assert isinstance(provider, EnvSecretsProvider)
