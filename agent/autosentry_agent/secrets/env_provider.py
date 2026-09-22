import os

from .base import SecretsProvider


class EnvSecretsProvider(SecretsProvider):
    def get_secret(self, name: str) -> str:
        return os.environ[name]

    def get_secret_version(self, name: str) -> str | None:
        return None
