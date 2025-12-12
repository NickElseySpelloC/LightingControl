import gzip
import json

import requests
from sc_utility import JSONEncoder, SCConfigManager, SCLogger

HTTP_STATUS_FORBIDDEN = 403


def post_state_to_web_viewer(config: SCConfigManager, logger: SCLogger, system_state: dict) -> None:
    """Post the LightingController state to the web server if WebsiteBaseURL is set in config.

    Args:
        config: The configuration manager instance.
        logger: The logger instance for logging messages.
        system_state: The state data to be posted.
    """
    is_enabled = config.get("ViewerWebsite", "Enable", default=False)
    base_url = config.get("ViewerWebsite", "BaseURL", default=None)
    access_key = config.get("ViewerWebsite", "AccessKey", default=None)
    timeout_wait = config.get("ViewerWebsite", "APITimeout", default=5)

    if not is_enabled or base_url is None:
        return

    # Convert the system state to a JSON-ready dict
    if not isinstance(system_state, (dict, list)):
        logger.log_fatal_error("System state must be a dict or list to post to web viewer.")
        return
    try:
        json_data = JSONEncoder.ready_dict_for_json(system_state)
        json_str = json.dumps(json_data)
        compressed_data = gzip.compress(json_str.encode("utf-8"))
    except RuntimeError as e:
        logger.log_fatal_error(f"Failed to prepare system state for JSON: {e}")

    api_url = base_url + "/api/submit"  # pyright: ignore[reportOperatorIssue]
    if access_key:
        api_url += f"?key={access_key}"
    headers = {
        "Content-Type": "application/json",
        "Content-Encoding": "gzip",
    }
    try:
        response = requests.post(api_url, headers=headers, data=compressed_data, timeout=timeout_wait)  # type: ignore[call-arg]
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        try:
            returned_json = response.json()  # pyright: ignore[reportPossiblyUnboundVariable]
        except (ValueError, requests.exceptions.JSONDecodeError):
            returned_json = response.text if hasattr(response, "text") else "No response content"    # pyright: ignore[reportPossiblyUnboundVariable]
        if response.status_code == HTTP_STATUS_FORBIDDEN:  # pyright: ignore[reportPossiblyUnboundVariable]
            logger.log_message(f"Access denied ({HTTP_STATUS_FORBIDDEN} Forbidden) when posting to {api_url}. Check your access key or permissions. Error: {e}, Response: {returned_json}", "error")
        else:
            logger.log_message(f"HTTP error saving state to web server at {api_url}: Error: {e}, Response: {returned_json}", "warning")
    except requests.exceptions.ConnectionError as e:
        logger.log_message(f"Web server at {api_url} is unavailable. Error: {e}", "warning")
    except requests.exceptions.Timeout as e:
        logger.log_message(f"Timeout while trying to save state to web server at {api_url}: Error: {e}", "warning")
    except requests.exceptions.RequestException as e:
        try:
            returned_json = response.json()  # pyright: ignore[reportPossiblyUnboundVariable]
        except (ValueError, requests.exceptions.JSONDecodeError, UnboundLocalError):
            returned_json = response.text if hasattr(response, "text") else "No response content"  # pyright: ignore[reportPossiblyUnboundVariable]
        logger.log_fatal_error(f"Error saving state to web server at {api_url}: Error: {e}, Response: {returned_json}")
    else:
        # Record the time of the last post even if it failed so that we don't keep retrying on errors
        # self.logger.log_message(f"Posted state for {system_state.get('DeviceName')} to {api_url}.", "debug")
        pass
