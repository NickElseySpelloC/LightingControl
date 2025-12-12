"""Main module for the Lighting Control app."""

import platform
import signal
import sys
from functools import partial

from sc_utility import SCConfigManager, SCLogger

from config_schemas import ConfigSchema
from controller import LightingController

CONFIG_FILE = "config.yaml"


def _graceful_shutdown(logger: SCLogger, controller: LightingController, sig: int, frame) -> None:  # noqa: ARG001
    """Handle SIGINT/SIGTERM for clean shutdown."""
    try:
        if logger is not None:
            logger.log_message(f"Received signal {sig}. Shutting down gracefully...", "summary")
        if controller is not None:
            controller.shutdown()

    finally:
        # Exit to stop Flask dev server loop cleanly
        sys.exit(0)


def _register_signal_handlers(logger: SCLogger, controller: LightingController) -> None:
    """Register SIGINT/SIGTERM handlers."""
    handler = partial(_graceful_shutdown, logger, controller)

    try:
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
    except Exception:  # noqa: BLE001
        # Some environments (e.g., threads or certain servers) may restrict signals
        if logger is not None:
            logger.log_message("Could not register signal handlers.", "detailed")


def main():
    """Main function."""
    print(f"Starting the Lighting Control, running on {platform.system()}")

    # Get our default schema, validation schema, and placeholders
    schemas = ConfigSchema()

    # Initialize the SC_ConfigManager class
    try:
        config = SCConfigManager(
            config_file=CONFIG_FILE,
            validation_schema=schemas.validation,
            placeholders=schemas.placeholders
        )
    except RuntimeError as e:
        print(f"Configuration file error: {e}", file=sys.stderr)
        sys.exit(1)     # Exit with errorcode 1 so that launch.sh can detect it

    # Initialize the SC_Logger class
    try:
        logger = SCLogger(config.get_logger_settings())
    except RuntimeError as e:
        print(f"Logger initialisation error: {e}", file=sys.stderr)
        sys.exit(1)     # Exit with errorcode 1 so that launch.sh can detect it

    # Setup email
    email_settings = config.get_email_settings()
    if email_settings is not None:
        logger.register_email_settings(email_settings)

    # If the prior run fails, send email that this run worked OK
    if logger.get_fatal_error():
        logger.log_message("Run was successful after a prior failure.", "summary")
        logger.clear_fatal_error()
        logger.send_email("LightingControl recovery", "LightingControl run was successful after a prior failure.")

    # Initialize the LightingControl class
    try:
        controller = LightingController(config, logger)

        # Register signal handlers for clean shutdown
        _register_signal_handlers(logger, controller)

        # Run the main loop
        controller.run()

    except (RuntimeError, Exception) as e:  # noqa: BLE001
        # Handle any other untrapped exception
        main_fatal_error = f"LightingControl terminated unexpectedly due to unexpected error: {e}"
        controller.ping_heatbeat(is_fail=True)
        logger.log_fatal_error(main_fatal_error, report_stack=True)


if __name__ == "__main__":
    main()
