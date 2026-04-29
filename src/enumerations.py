"""Enumerations for the LightingControl application."""
from enum import StrEnum


class AppMode(StrEnum):
    """Override mode for webapp group and switch controls."""

    ON = "on"
    OFF = "off"
    AUTO = "auto"


class SystemState(StrEnum):
    """Overall evaluated state of a lighting switch."""

    SCHEDULED = "Automatic control based on schedule"
    WEBAPP_SWITCH_OVERRIDE = "Webapp has overridden this switch"
    WEBAPP_GROUP_OVERRIDE = "Webapp has overridden the group"
    INPUT_OVERRIDE = "Input switch has overridden the schedule"
    DATE_OFF = "DatesOff condition met for today"


class StateReasonOn(StrEnum):
    """Reasons why a switch is on."""

    SCHEDULED_ON = "Schedule dictates on"
    WEBAPP_SWITCH_ON = "Webapp switch mode set to On"
    WEBAPP_GROUP_ON = "Webapp group mode set to On"
    INPUT_SWITCH_ON = "Input switch is overriding the schedule to On"


class StateReasonOff(StrEnum):
    """Reasons why a switch is off."""

    SCHEDULED_OFF = "Schedule dictates off"
    WEBAPP_SWITCH_OFF = "Webapp switch mode set to Off"
    WEBAPP_GROUP_OFF = "Webapp group mode set to Off"
    INPUT_SWITCH_OFF = "Input switch reverted to schedule off"
    DATE_OFF = "DatesOff condition met for today"
    DEVICE_OFFLINE = "Device is offline"
    SHUTDOWN = "System is shutting down"
