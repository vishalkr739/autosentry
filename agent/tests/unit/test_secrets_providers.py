import pytest
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError
from autosentry_agent.secrets.env_provider import EnvSecretsProvider
from autosentry_agent.secrets.aws_provider import AwsSecretsManagerProvider
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

@patch('autosentry_agent.secrets.aws_provider.boto3.client')
def test_aws_provider_version_returns_none_when_no_awscurrent_stage(mock_boto3_client):
    """Test that get_secret_version returns None when VersionIdsToStages exists but has no AWSCURRENT stage."""
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client

    provider = AwsSecretsManagerProvider()
    provider._client.describe_secret.return_value = {
        "VersionIdsToStages": {
            "v1": ["AWSPREVIOUS"],
            "v2": ["AWSPENDING"]
        }
    }

    result = provider.get_secret_version("test-secret")
    assert result is None

@patch('autosentry_agent.secrets.aws_provider.boto3.client')
def test_aws_provider_get_secret_happy_path(mock_boto3_client):
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client

    provider = AwsSecretsManagerProvider()
    provider._client.get_secret_value.return_value = {"SecretString": "the-value"}

    assert provider.get_secret("test-secret") == "the-value"

@patch('autosentry_agent.secrets.aws_provider.boto3.client')
def test_aws_provider_get_secret_missing_raises_key_error(mock_boto3_client):
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client

    provider = AwsSecretsManagerProvider()
    provider._client.get_secret_value.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
        "GetSecretValue",
    )

    with pytest.raises(KeyError):
        provider.get_secret("test-secret")

@patch('autosentry_agent.secrets.aws_provider.boto3.client')
def test_aws_provider_get_secret_reraises_other_client_errors(mock_boto3_client):
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client

    provider = AwsSecretsManagerProvider()
    provider._client.get_secret_value.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetSecretValue",
    )

    with pytest.raises(ClientError):
        provider.get_secret("test-secret")

@patch('autosentry_agent.secrets.aws_provider.boto3.client')
def test_aws_provider_get_secret_raises_key_error_for_binary_secret(mock_boto3_client):
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client

    provider = AwsSecretsManagerProvider()
    provider._client.get_secret_value.return_value = {"SecretBinary": b"binary-data"}

    with pytest.raises(KeyError):
        provider.get_secret("test-secret")

@patch('autosentry_agent.secrets.aws_provider.boto3.client')
def test_aws_provider_version_returns_version_when_awscurrent_present(mock_boto3_client):
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client

    provider = AwsSecretsManagerProvider()
    provider._client.describe_secret.return_value = {
        "VersionIdsToStages": {"v1": ["AWSCURRENT"]}
    }

    assert provider.get_secret_version("test-secret") == "v1"

def test_factory_returns_aws_provider_for_aws_backend(monkeypatch):
    monkeypatch.setenv("SECRETS_BACKEND", "aws")
    with patch('autosentry_agent.secrets.aws_provider.boto3.client') as mock_boto3_client:
        mock_boto3_client.return_value = MagicMock()
        provider = get_secrets_provider()
    assert isinstance(provider, AwsSecretsManagerProvider)
