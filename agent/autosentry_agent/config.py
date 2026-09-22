import os

from .secrets.aws_provider import AwsSecretsManagerProvider
from .secrets.base import SecretsProvider
from .secrets.env_provider import EnvSecretsProvider


class SecretsBackendError(RuntimeError):
    """Raised when SECRETS_BACKEND is set to an unknown value. Fails
    closed: never falls back to a default backend silently."""


def get_secrets_provider() -> SecretsProvider:
    backend = os.environ.get("SECRETS_BACKEND", "env")
    if backend == "env":
        return EnvSecretsProvider()
    if backend == "aws":
        return AwsSecretsManagerProvider()
    raise SecretsBackendError(
        f"Unknown SECRETS_BACKEND={backend!r}; expected 'env' or 'aws'."
    )
