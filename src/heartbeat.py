"""Heartbeat reporting for the HeartbeatMonitor service.

Call report_healthy() each controller cycle and report_fatal() on unrecoverable errors.
Both are no-ops when HeartbeatMonitor.WebsiteURL is not configured.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from sc_foundation import SCConfigManager


def report_healthy(config: SCConfigManager) -> bool:
    """Ping the heartbeat monitor's healthy endpoint.

    Args:
        config (SCConfigManager): The configuration manager to read the URL and timeout from.

    Returns:
       bool: True on success or when no URL is configured.
    """
    url = config.get("HeartbeatMonitor", "WebsiteURL")
    if url is None:
        return True
    assert isinstance(url, str)
    timeout = config.get("HeartbeatMonitor", "HeartbeatTimeout", default=10)
    try:
        response = requests.get(url, timeout=timeout)  # type: ignore[call-arg]
    except requests.RequestException:
        return False
    else:
        return response.status_code == 200


def report_fatal(config: SCConfigManager) -> None:
    """Ping the heartbeat monitor's fail endpoint.

    Silently ignored when no URL is configured or the request fails.
    """
    url = config.get("HeartbeatMonitor", "WebsiteURL")
    if url is None:
        return
    assert isinstance(url, str)
    timeout = config.get("HeartbeatMonitor", "HeartbeatTimeout", default=10)
    try:
        requests.get(url + "/fail", timeout=timeout)  # type: ignore[call-arg]
    except requests.RequestException:
        return
