import os
import pytest


def pytest_configure(config):
    """Set a dummy DATABASE_URL before test collection."""
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
