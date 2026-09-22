from abc import ABC, abstractmethod


class SecretsProvider(ABC):
    @abstractmethod
    def get_secret(self, name: str) -> str:
        """Return the current value of the named secret. Raises KeyError
        if the secret does not exist."""

    @abstractmethod
    def get_secret_version(self, name: str) -> str | None:
        """Return an opaque version marker for the secret, or None if this
        backend has no concept of versioning (rotation detection then
        becomes a no-op, per spec section 1.3)."""
