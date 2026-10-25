"""Pytest configuration for SAGE tests."""

import pytest
from app.config import settings


def pytest_configure(config):
    """Disable workspace authorization for all tests."""
    settings.disable_workspace_auth = True


def pytest_unconfigure(config):
    """Restore settings after tests."""
    settings.disable_workspace_auth = False