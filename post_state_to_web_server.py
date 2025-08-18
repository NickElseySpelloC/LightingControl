import requests

HTTP_STATUS_FORBIDDEN = 403


def post_state_to_web_server(config, logger, state_data, config_section: str | None = "General"):
    """Post the LightingController state to the web server if WebsiteBaseURL is set in config.

    Args:
        config: The configuration manager instance.
        logger: The logger instance for logging messages.
        state_data: The state data to be posted.
        config_section: The configuration section to use (default is "General").
    """
    base_url = config.get(config_section, "WebsiteBaseURL", default=None)
    access_key = config.get(config_section, "WebsiteAccessKey")
    timeout_wait = config.get(config_section, "WebsiteTimeout", default=5)
    if base_url:
        api_url = base_url + "/api/submit"
        if access_key:
            api_url += f"?key={access_key}"
        headers = {
            "Content-Type": "application/json",
        }
        json_object = state_data
        try:
            response = requests.post(api_url, headers=headers, json=json_object, timeout=timeout_wait)
            response.raise_for_status()
            logger.log_message(f"Posted LightingController state to {api_url}", "debug")
        except requests.exceptions.HTTPError as e:
            try:
                returned_json = response.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                returned_json = response.text if hasattr(response, "text") else "No response content"
            if response.status_code == HTTP_STATUS_FORBIDDEN:
                logger.log_message(f"Access denied ({HTTP_STATUS_FORBIDDEN} Forbidden) when posting to {api_url}. Check your access key or permissions. Error: {e}, Response: {returned_json}", "error")
            else:
                logger.log_message(f"HTTP error saving state to web server at {api_url}: Error: {e}, Response: {returned_json}", "warning")
        except requests.exceptions.ConnectionError as e:
            logger.log_message(f"Web server at {api_url} is unavailable. Error: {e}", "warning")
        except requests.exceptions.Timeout as e:
            logger.log_message(f"Timeout while trying to save state to web server at {api_url}: Error: {e}", "warning")
        except requests.exceptions.RequestException as e:
            try:
                returned_json = response.json()
            except (ValueError, requests.exceptions.JSONDecodeError, UnboundLocalError):
                returned_json = response.text if hasattr(response, "text") else "No response content"
            logger.log_fatal_error(f"Error saving state to web server at {api_url}: Error: {e}, Response: {returned_json}")
