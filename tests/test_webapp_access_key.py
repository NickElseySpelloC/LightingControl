"""Tests for web app access key validation (issue #14)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from webapp import _validate_access_key


@pytest.fixture()
def config():
    m = MagicMock()
    m.get.return_value = None  # No AccessKey in config by default
    return m


@pytest.fixture()
def logger():
    return MagicMock()


def test_no_key_configured_allows_all(config, logger, monkeypatch):
    """When no access key is configured, all requests are allowed."""
    monkeypatch.delenv("WEBAPP_ACCESS_KEY", raising=False)
    assert _validate_access_key(config, logger, None) is True
    assert _validate_access_key(config, logger, "anything") is True


def test_request_blocked_without_key(config, logger, monkeypatch):
    """When access key is required, a request without a key is blocked."""
    monkeypatch.setenv("WEBAPP_ACCESS_KEY", "secret123")
    assert _validate_access_key(config, logger, None) is False


def test_request_blocked_with_wrong_key(config, logger, monkeypatch):
    """When access key is required, a request with an incorrect key is blocked."""
    monkeypatch.setenv("WEBAPP_ACCESS_KEY", "secret123")
    assert _validate_access_key(config, logger, "wrongkey") is False


def test_request_allowed_with_correct_key(config, logger, monkeypatch):
    """When access key is required, a request with the correct key is allowed."""
    monkeypatch.setenv("WEBAPP_ACCESS_KEY", "secret123")
    assert _validate_access_key(config, logger, "secret123") is True


def test_access_key_from_config_honoured(monkeypatch):
    """Access key read from config (not env var) is enforced."""
    monkeypatch.delenv("WEBAPP_ACCESS_KEY", raising=False)
    config = MagicMock()
    config.get.return_value = "configkey"
    logger = MagicMock()
    assert _validate_access_key(config, logger, None) is False
    assert _validate_access_key(config, logger, "configkey") is True


def test_blank_env_key_allows_all(config, logger, monkeypatch):
    """A blank/whitespace-only WEBAPP_ACCESS_KEY means no protection."""
    monkeypatch.setenv("WEBAPP_ACCESS_KEY", "   ")
    assert _validate_access_key(config, logger, None) is True
