"""Configuration schemas for use with the SCConfigManager class."""


class ConfigSchema:
    """Base class for configuration schemas."""

    def __init__(self):

        self.placeholders = {
            "Email": {
                "SMTPUsername": "<Your SMTP username here>",
                "SMTPPassword": "<Your SMTP password here>",
            }
        }

        self.validation = {
            "General": {
                "type": "dict",
                "required": False,
                "nullable": True,
                "schema": {
                    "AppName": {"type": "string", "required": False, "nullable": True},
                    "CheckInterval": {"type": "number", "required": False, "nullable": True},
                    "WebsiteBaseURL": {"type": "string", "required": False, "nullable": True},
                    "WebsiteAccessKey": {"type": "string", "required": False, "nullable": True},
                    "WebsiteTimeout": {"type": "number", "required": False, "nullable": True},
                },
            },
            "ShellyDevices": {
                "type": "dict",
                "schema": {
                    "ResponseTimeout": {"type": "number", "required": False, "nullable": True, "min": 1, "max": 120},
                    "RetryCount": {"type": "number", "required": False, "nullable": True, "min": 0, "max": 10},
                    "RetryDelay": {"type": "number", "required": False, "nullable": True, "min": 1, "max": 10},
                    "PingAllowed": {"type": "boolean", "required": False, "nullable": True},
                    "Devices": {
                        "type": "list",
                        "required": True,
                        "nullable": False,
                        "schema": {
                            "type": "dict",
                            "schema": {
                                "Name": {"type": "string", "required": False, "nullable": True},
                                "Model": {"type": "string", "required": True},
                                "Hostname": {"type": "string", "required": False, "nullable": True},
                                "Port": {"type": "number", "required": False, "nullable": True},
                                "ID": {"type": "number", "required": False, "nullable": True},
                                "Simulate": {"type": "boolean", "required": False, "nullable": True},
                                "Inputs": {
                                    "type": "list",
                                    "required": False,
                                    "nullable": True,
                                    "schema": {
                                        "type": "dict",
                                        "schema": {
                                            "Name": {"type": "string", "required": False, "nullable": True},
                                            "ID": {"type": "number", "required": False, "nullable": True},
                                        },
                                    },
                                },
                                "Outputs": {
                                    "type": "list",
                                    "required": False,
                                    "nullable": True,
                                    "schema": {
                                        "type": "dict",
                                        "schema": {
                                            "Name": {"type": "string", "required": False, "nullable": True},
                                            "Group": {"type": "string", "required": False, "nullable": True},
                                            "ID": {"type": "number", "required": False, "nullable": True},
                                        },
                                    },
                                },
                                "Meters": {
                                    "type": "list",
                                    "required": False,
                                    "nullable": True,
                                    "schema": {
                                        "type": "dict",
                                        "schema": {
                                            "Name": {"type": "string", "required": False, "nullable": True},
                                            "ID": {"type": "number", "required": False, "nullable": True},
                                        },
                                    },
                                },
                            },
                        },
                    },
                }
            },
            "Location": {
                "type": "dict",
                "required": False,
                "nullable": True,
                "schema": {
                    "Name": {"type": "string", "required": True},
                    "GoogleMapsURL": {"type": "string", "required": False, "nullable": True},
                    "Timezone": {"type": "string", "required": True},
                    "Latitude": {"type": "number", "required": False, "nullable": True},
                    "Longitude": {"type": "number", "required": False, "nullable": True},
                },
            },
            "Schedules": {
                "type": "list",
                "required": True,
                "nullable": False,
                "schema": {
                    "type": "dict",
                    "schema": {
                        "Name": {"type": "string", "required": True},
                        "Events": {
                            "type": "list",
                            "required": True,
                            "schema": {
                                "type": "dict",
                                "schema": {
                                    "TurnOn": {"type": "string", "required": True},
                                    "TurnOff": {"type": "string", "required": True},
                                    "RandomOffset": {"type": "number", "required": False, "nullable": True},
                                    "DaysOfWeek": {"type": "string", "required": False, "nullable": True},
                                    "DatesOff": {
                                        "type": "list",
                                        "required": False,
                                        "nullable": True,
                                        "schema": {
                                            "type": "dict",
                                            "schema": {
                                                "StartDate": {"type": "date", "required": False, "nullable": True},
                                                "EndDate": {"type": "date", "required": False, "nullable": True},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "LightingControl": {
                "type": "list",
                "required": True,
                "nullable": False,
                "schema": {
                    "type": "dict",
                    "schema": {
                        "Type": {"type": "string", "required": True},
                        "Target": {"type": "string", "required": False, "nullable": True},
                        "Schedule": {"type": "string", "required": True},
                    },
                },
            },
            "Files": {
                "type": "dict",
                "schema": {
                    "SavedStateFile": {"type": "string", "required": True},
                    "LogfileName": {"type": "string", "required": False, "nullable": True},
                    "LogProcessID": {"type": "boolean", "required": False, "nullable": True},
                    "LogfileMaxLines": {"type": "number", "required": False, "nullable": True, "min": 0, "max": 100000},
                    "LogfileVerbosity": {"type": "string", "required": True, "allowed": ["none", "error", "warning", "summary", "detailed", "debug", "all"]},
                    "ConsoleVerbosity": {"type": "string", "required": True, "allowed": ["error", "warning", "summary", "detailed", "debug"]},
                    "MaxDaysSwitchChangeHistory": {"type": "number", "required": False, "nullable": True, "min": 1, "max": 365},
                },
            },
            "Email": {
                "type": "dict",
                "schema": {
                    "EnableEmail": {"type": "boolean", "required": False, "nullable": True},
                    "SendEmailsTo": {"type": "string", "required": False, "nullable": True},
                    "SMTPServer":  {"type": "string", "required": False, "nullable": True},
                    "SMTPPort": {"type": "number", "required": False, "nullable": True, "min": 25, "max": 10000},
                    "SMTPUsername": {"type": "string", "required": False, "nullable": True},
                    "SMTPPassword": {"type": "string", "required": False, "nullable": True},
                    "SubjectPrefix": {"type": "string", "required": False, "nullable": True},
                },
            },
            "HeartbeatMonitor": {
                "type": "dict",
                "schema": {
                    "WebsiteURL": {"type": "string", "required": False, "nullable": True},
                    "HeartbeatTimeout": {"type": "number", "required": False, "nullable": True, "min": 1, "max": 60},
                },
            },
        }
