import os

from pydantic_settings import BaseSettings, SettingsConfigDict

from .secrets.aws_provider import AwsSecretsManagerProvider
from .secrets.base import SecretsProvider
from .secrets.env_provider import EnvSecretsProvider


class SecretsBackendError(RuntimeError):
    """Raised when SECRETS_BACKEND is set to an unknown value. Fails
    closed: never falls back to a default backend silently."""


class GraphDataSettings(BaseSettings):
    """Where tigergraph-mcp runs and which TigerGraph it should reach.

    Non-secret, read from the environment; names follow savanna-agent's
    `.env.example`. Credentials are not here: the JWT (or, outside prod, the
    password) comes from the SecretsProvider on every session.
    """

    model_config = SettingsConfigDict(extra="ignore")

    autosentry_env: str = "dev"
    graph_data_mcp_url: str = "http://localhost:8010"
    graph_data_mcp_timeout_seconds: float = 60.0
    graph_data_ready_cache_seconds: float = 10.0
    tg_host: str = "http://localhost:14240"
    tg_graphname: str = "AutosentrySandbox"
    tg_username: str = ""
    tg_version: str = ""
    # The workspace tools-server proxy paths, when calls go through one.
    tg_gsql_prefix: str = ""
    tg_restpp_prefix: str = ""


def get_secrets_provider() -> SecretsProvider:
    backend = os.environ.get("SECRETS_BACKEND", "env")
    if backend == "env":
        return EnvSecretsProvider()
    if backend == "aws":
        return AwsSecretsManagerProvider()
    raise SecretsBackendError(
        f"Unknown SECRETS_BACKEND={backend!r}; expected 'env' or 'aws'."
    )
