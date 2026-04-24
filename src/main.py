"""Main module for the Lighting Control app."""

import argparse
import os
import platform
import signal
import sys
from functools import partial
from pathlib import Path

from mergedeep import merge
from sc_foundation import SCCommon, SCConfigManager, SCLogger
from sc_smart_device import smart_devices_validator

from config_schemas import ConfigSchema
from controller import LightingController

CONFIG_FILE = "config.yaml"


def parse_command_line_args() -> dict[str, str | None]:
    """Parse and validate command line arguments.

    Returns:
        dict: Dictionary containing parsed arguments with keys:
            - 'config_file': Path to configuration file (always present)
            - 'homedir': Project home directory (for logging purposes, may be None)

    Exits:
        Exits with code 1 if arguments are invalid.
    """
    parser = argparse.ArgumentParser(
        description="LightingControl - Intelligent lighting management system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --config /path/to/config.yaml
  python main.py --homedir /opt/lightingcontrol --config config.yaml
        """
    )

    parser.add_argument(
        "--homedir",
        type=str,
        metavar="PATH",
        help="Specify the project home directory",
    )

    parser.add_argument(
        "--config",
        type=str,
        metavar="FILE",
        help=f"Path to configuration file (default: {CONFIG_FILE})",
    )

    args = parser.parse_args()

    # Determine the base directory for resolving relative paths
    if args.homedir:
        homedir = Path(args.homedir)
        if not homedir.exists():
            print(f"ERROR: Specified homedir does not exist: {args.homedir}", file=sys.stderr)
            sys.exit(1)
        if not homedir.is_dir():
            print(f"ERROR: Specified homedir is not a directory: {args.homedir}", file=sys.stderr)
            sys.exit(1)
        base_dir = homedir.resolve()

        # Set the project root environment variable for use by sc-foundation and other components
        os.environ["SC_FOUNDATION_PROJECT_ROOT"] = str(base_dir)
    else:
        base_dir = Path(SCCommon.get_project_root())

    # Determine the config file path
    if args.config:
        config_path = Path(args.config)
        # If relative path, resolve it relative to base_dir
        if not config_path.is_absolute():
            config_path = base_dir / config_path
        config_file = str(config_path.resolve())

        # Validate that the config file exists
        if not Path(config_file).exists():
            print(f"ERROR: Configuration file does not exist: {config_file}", file=sys.stderr)
            sys.exit(1)
        if not Path(config_file).is_file():
            print(f"ERROR: Configuration path is not a file: {config_file}", file=sys.stderr)
            sys.exit(1)
    else:
        config_file = CONFIG_FILE

    return {
        "config_file": config_file,
        "homedir": str(base_dir) if args.homedir else None,
    }


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

    # Parse command line arguments
    cmd_args = parse_command_line_args()

    # Get our default schema, validation schema, and placeholders
    schemas = ConfigSchema()

    # Merge the SmartDevices validation schema with the default validation schema
    merged_schema = merge({}, smart_devices_validator, schemas.validation)
    assert isinstance(merged_schema, dict), "Merged schema should be type dict"

    # Initialize the SC_ConfigManager class
    try:
        config_file = cmd_args["config_file"]
        assert isinstance(config_file, str), "config_file must be a string"
        config = SCConfigManager(
            config_file=config_file,
            validation_schema=merged_schema,
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
    else:
        logger.log_message("", "summary")
        logger.log_message("", "summary")
        logger.log_message("LightingControl application starting.", "summary")
        if cmd_args["homedir"]:
            logger.log_message(f"Home directory: {cmd_args['homedir']}", "debug")
        logger.log_message(f"Configuration file: {cmd_args['config_file']}", "debug")

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
