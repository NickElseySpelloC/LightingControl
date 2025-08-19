
import json
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Optional

from sc_utility import SCLogger

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8787
DEFAULT_PATH = "/shelly/webhook"


class WebhookConfig:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, path: str = DEFAULT_PATH):
        self.host = host
        self.port = int(port)
        # Normalize path to start with '/'
        self.path = path if path.startswith('/') else f'/{path}'


class _ShellyWebhookHandler(BaseHTTPRequestHandler):
    # `server` attribute will be a ThreadingHTTPServer with `.controller` and `.config_path` attributes attached.

    def _ok(self, body: bytes = b"OK"):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Simple health check
        self._ok(b"lighting-control webhook up")

    def do_POST(self):
        # Only gate on path if the user configured one (default allows specific path)
        try:
            expected_path = getattr(self.server, "webhook_path", DEFAULT_PATH)
        except Exception:
            expected_path = DEFAULT_PATH

        if expected_path and self.path.split("?")[0] != expected_path:
            # Allow 404 for wrong path to make it easier to separate multiple endpoints
            self.send_error(404, "Not Found")
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""

        # Try to parse JSON, but fall back to text. We just print to console for now.
        parsed: Optional[object] = None
        try:
            parsed = json.loads(raw.decode("utf-8") if raw else "null")
        except Exception:
            parsed = None

        print("\n=== Shelly Webhook Received ===")
        print(f"Path: {self.path}")
        try:
            # This may include sensitive headers; printing selectively.
            print("Headers:")
            for k, v in self.headers.items():
                print(f"  {k}: {v}")
        except Exception:
            pass
        if parsed is not None:
            print("Body (JSON):")
            print(json.dumps(parsed, indent=2, ensure_ascii=False))
        else:
            print("Body (raw):")
            try:
                print(raw.decode("utf-8", errors="replace"))
            except Exception:
                print(f"<{len(raw)} bytes>")

        # Wake the controller's run loop immediately
        try:
            controller = getattr(self.server, "controller", None)
            if controller is not None and getattr(controller, "wake_event", None) is not None:
                controller.wake_event.set()
        except Exception:
            # Don't fail webhook because of controller issues.
            pass

        self._ok()

    # Silence default logging to avoid duplicate lines; we already print above.
    def log_message(self, format, *args):
        return


def start_webhook_server(controller, logger: SCLogger, cfg: Optional[WebhookConfig] = None) -> ThreadingHTTPServer | None:
    """Start the webhook server in a background thread.
    Args:
        controller: The LightingController instance to handle webhook events.
        logger: The SCLogger instance for logging.
        cfg: Optional WebhookConfig to override default settings.

    Returns:
        ThreadingHTTPServer instance running the webhook server.

    Raises:
        RuntimeError: If the server fails to start.
    """
    cfg = cfg or WebhookConfig()
    try:
        server = ThreadingHTTPServer((cfg.host, cfg.port), _ShellyWebhookHandler)
        server.controller = controller  # type: ignore[attr-defined]
        server.webhook_path = cfg.path  # type: ignore[attr-defined]

        t = threading.Thread(target=server.serve_forever, daemon=True, name="ShellyWebhookServer")
        t.start()
    except (RuntimeError, OSError, TypeError) as e:
        logger.log_fatal_error(f"Failed to start webhook server: {e}")
        return None
    else:
        logger.log_message(f"Webhook server started on http://{cfg.host}:{cfg.port}{cfg.path}", "debug")
        return server
