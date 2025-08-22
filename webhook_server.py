
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from sc_utility import SCConfigManager, SCLogger

from controller import LightingController

DEFAULT_HOST = "127.0.01"
DEFAULT_PORT = 8787
DEFAULT_PATH = "/shelly/webhook"


class WebhookConfig:
    def __init__(self, config: SCConfigManager):
        self.host = config.get("InputWebhooks", "Host", default=DEFAULT_HOST) or DEFAULT_HOST
        self.port = int(config.get("InputWebhooks", "Port", default=DEFAULT_PORT) or DEFAULT_PORT)  # pyright: ignore[reportArgumentType]
        # Normalize path to start with '/'
        path = str(config.get("InputWebhooks", "Path", default=DEFAULT_PATH) or DEFAULT_PATH)
        self.path = path if path.startswith("/") else f"/{path}"


class _ShellyWebhookHandler(BaseHTTPRequestHandler):
    # `server` attribute will be a ThreadingHTTPServer with `.controller` and `.config_path` attributes attached.

    @property
    def config(self) -> SCConfigManager:
        return getattr(self.server, "config", None)  # pyright: ignore[reportReturnType]

    @property
    def logger(self) -> SCLogger:
        return getattr(self.server, "logger", None)  # pyright: ignore[reportReturnType]

    def _ok(self, body: bytes = b"OK"):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        # Validate path like do_POST
        try:
            expected_path = getattr(self.server, "webhook_path", DEFAULT_PATH)
        except AttributeError:
            expected_path = DEFAULT_PATH

        # Split path and query
        path_only = self.path.split("?")[0]
        query_string = self.path[len(path_only):].lstrip("?") if "?" in self.path else ""

        if expected_path and path_only != expected_path:
            self.send_error(404, "Not Found")
            return

        if query_string:
            args = parse_qs(query_string)
            self.logger.log_message(f"Received webhook GET request {self.path}. Arguments: {args}.", "debug")
        else:
            args = {}
            self.logger.log_message(f"Received webhook GET request {self.path} with no arguments.", "debug")

        # Wake the controller's run loop immediately
        try:
            controller = getattr(self.server, "controller", None)
            if controller is not None and getattr(controller, "wake_event", None) is not None:
                # Add self.path as the first key/value pair to args
                args["path"] = self.path  # pyright: ignore[reportArgumentType]
                controller.webhook_args = args
                controller.wake_event.set()
        except AttributeError:
            pass

        self._ok(b"LightingController webhook up")


def install_shelly_webhooks(controller: LightingController, logger: SCLogger, cfg: WebhookConfig) -> None:  # noqa: PLR0912, PLR0915
    """Install the input trigger webhooks for Shelly devices.

    Args:
        controller: The lighting controller instance.
        logger: The logger instance.
        cfg: The webhook configuration.

    Raises:
        TimeoutError: There's no response from the switch.
        RuntimeError: If the webhook installation fails.
    """
    if not cfg:
        logger.log_fatal_error("No webhook configuration provided when calling install_shelly_webhooks.")
        return

    # Look through each device in the controller
    for device in controller.shelly_control.devices:
        # Build a consolidated object of the device including its inputs
        device_info = controller.shelly_control.get_device_information(device)

        # Skip the device if it has no inputs
        if device.get("Inputs", 0) == 0:
            logger.log_message(f"Device {device.get('Name')} has no inputs, skipping webhook installation.", "debug")
            continue

        # Enumerate the device's inputs and see if we can find any of these in the switch_states list
        for device_input in device_info.get("Inputs", []):
            input_name = device_input.get("Name")
            if input_name not in controller.switch_states:
                logger.log_message(f"Input {input_name} on device {device.get('Name')} not associated with any switch_states, skipping webhook installation for this input.", "debug")
                continue

        # Skip if device generation 1 (REST)
        if device.get("Protocol") != "RPC":
            logger.log_message(f"Skipping webhook installation for device {device.get('Name')} - not an RPC device.", "warning")
            continue

        # Skip if device is in simulation mode or offline
        if device.get("Simulate", False) or not device.get("Online", False):
            logger.log_message(f"Skipping webhook installation for device {device.get('Name')} - device is offline or in simulation mode.", "warning")
            continue

        # If we get this far, we have at least one input on this device mentioned in our configuration, so install the webhooks
        try:
            # First do a Webhook.ListSupported call and make sure our webhooks are supported
            payload = {"id": 0,
                       "method": "Webhook.ListSupported"}
            result, result_data = controller.shelly_control._rpc_request(device, payload)  # noqa: SLF001
            if not result:
                logger.log_message(f"Failed to list supported web hooks for device {device.get('Name')}: {result_data}", "error")
                continue

            # The result_data should contain our supported webhook types
            supported_types = result_data.get("types", {})
            if ("input.toggle_on" not in supported_types) or ("input.toggle_off" not in supported_types):
                logger.log_message(f"Device {device.get('Name')} does not support the webhook types input.toggle_on and input.toggle_off. Skipping", "error")
                continue

            # Clear all existing web hooks for this device
            payload = {"id": 0,
                    "method": "Webhook.DeleteAll"}
            result, result_data = controller.shelly_control._rpc_request(device, payload)  # noqa: SLF001
            if not result:
                logger.log_message(f"Failed to delete existing web hooks for device {device.get('Name')}: {result_data}", "error")
                continue

            # Loop through the named inputs for this device
            for device_input in device_info.get("Inputs", []):
                input_name = device_input.get("Name")
                input_component = controller.shelly_control.get_device_component("input", input_name)

                # Now install the toggle on web hook
                payload = {"id": 0,
                        "method": "Webhook.Create",
                        "params": {"cid": input_component.get("ComponentIndex"),
                                    "enable": True,
                                    "event": "input.toggle_on",
                                    "name": f"Toggle On: {input_component.get('ComponentIndex')}",
                                    "urls": [f"http://{cfg.host}:{cfg.port}{cfg.path}?component={input_component.get('ComponentIndex')}&state=on"]}}
                result, result_data = controller.shelly_control._rpc_request(device, payload)  # noqa: SLF001
                if result:
                    logger.log_message(f"Installed toggle_on webhook rev {result_data.get('rev')} for input {input_component.get('Name')}", "debug")
                else:
                    logger.log_message(f"Failed to create input on web hooks for input {input_component.get('Name')}: {result_data}", "error")
                    continue

                # Now install the toggle off web hook
                payload = {"id": 0,
                        "method": "Webhook.Create",
                        "params": {"cid": input_component.get("ComponentIndex"),
                                    "enable": True,
                                    "event": "input.toggle_off",
                                    "name": f"Toggle Off: {input_component.get('ComponentIndex')}",
                                    "urls": [f"http://{cfg.host}:{cfg.port}{cfg.path}?component={input_component.get('ComponentIndex')}&state=off"]}}
                result, result_data = controller.shelly_control._rpc_request(device, payload)  # noqa: SLF001
                if result:
                    logger.log_message(f"Installed toggle_off webhook rev {result_data.get('rev')} for input {input_component.get('Name')}", "debug")
                else:
                    logger.log_message(f"Failed to create input on web hooks for input {input_component.get('Name')}: {result_data}", "error")
                    continue

        except TimeoutError as e:
            logger.log_message(f"Timeout error installing web hooks for device {device.get('Name')}: {e}", "error")
            raise TimeoutError(e) from e
        except RuntimeError as e:
            logger.log_message(f"Error installing web hooks for device {device.get('Name')}: {e}", "error")
            raise RuntimeError(e) from e


def start_webhook_server(controller: LightingController, config: SCConfigManager, logger: SCLogger) -> ThreadingHTTPServer | None:
    """Start the webhook server in a background thread.

    Args:
        controller: The LightingController instance to handle webhook events.
        config: The SCConfigManager instance for configuration settings.
        logger: The SCLogger instance for logging.

    Returns:
        ThreadingHTTPServer instance running the webhook server.
    """
    # Create the webhook configuration parameters
    cfg = WebhookConfig(config)

    install_shelly_webhooks(controller, logger, cfg)

    try:
        server = ThreadingHTTPServer((cfg.host, cfg.port), _ShellyWebhookHandler)  # pyright: ignore[reportArgumentType]
        server.controller = controller  # type: ignore[attr-defined]
        server.webhook_path = cfg.path  # type: ignore[attr-defined]
        server.config = config  # type: ignore[attr-defined]
        server.logger = logger  # type: ignore[attr-defined]

        t = threading.Thread(target=server.serve_forever, daemon=True, name="ShellyWebhookServer")
        t.start()
    except (RuntimeError, OSError, TypeError) as e:
        logger.log_fatal_error(f"Failed to start webhook server: {e}")
        return None
    else:
        logger.log_message(f"Webhook server started on http://{cfg.host}:{cfg.port}{cfg.path}", "debug")
        return server
