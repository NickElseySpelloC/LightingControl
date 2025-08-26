"""Main module for the Lighting Control app."""

import platform
import sys

from sc_utility import SCConfigManager, SCLogger

from config_schemas import ConfigSchema
from controller import LightingController

CONFIG_FILE = "config.yaml"


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

        # Run the main loop
        controller.run()

    except (RuntimeError, Exception) as e:
        # Handle any other untrapped exception
        main_fatal_error = f"LightingControl terminated unexpectedly due to unexpected error: {e}"
        controller.ping_heatbeat(is_fail=True)
        logger.log_fatal_error(main_fatal_error, report_stack=True)


if __name__ == "__main__":
    main()
