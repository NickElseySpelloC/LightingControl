import copy
import datetime as dt
import json
import operator
import random
import re
import time
from pathlib import Path

import pytz
import requests
from astral import LocationInfo
from astral.sun import sun
from sc_utility import DateHelper, SCCommon, SCConfigManager, SCLogger, ShellyControl

WEEKDAY_ABBREVIATIONS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HTTP_STATUS_FORBIDDEN = 403


class LightingController:
    def __init__(self, config: SCConfigManager, logger: SCLogger, shelly_control: ShellyControl):
        self.config = config
        self.logger = logger
        self.shelly_control = shelly_control
        self.dusk_dawn = {}
        self.schedule_map = {}
        self.state_filepath = None
        self.offset_cache = {}
        self.switch_states = []  # The current state of each switch
        self.switch_events = []  # List of switch change events for logging purposes. This is a list of days, and for each day, a list of events with the time, switch identifier and state change.
        self._initialise()

    def _initialise(self):
        """Initialise the controller, refreshing the state and config as needed."""
        self.dusk_dawn = self.get_dusk_dawn_times()
        self.schedule_map = self._map_schedules_to_outputs()
        state_file = self.config.get("Files", "SavedStateFile", default="system_state.json")
        self.state_filepath = SCCommon.select_file_location(state_file)  # type: ignore[attr-defined]
        self._load_state()

        self.check_interval = self.config.get("General", "CheckInterval", default=5) or 5

        self.logger.log_message("Lighting Controller initialised successfully.", "debug")

    def get_dusk_dawn_times(self) -> dict:
        """Get the dawn and dusk times based on the location configuration.

        Returns:
            dict: A dictionary with 'dawn' and 'dusk' times.
        """
        loc_conf = self.config.get("Location", default={})
        assert isinstance(loc_conf, dict), "Location configuration must be a dictionary"
        name = loc_conf.get("Name", "Unknown")
        tz = loc_conf["Timezone"]

        # Extract coordinates
        if "GoogleMapsURL" in loc_conf and loc_conf["GoogleMapsURL"] is not None:
            url = loc_conf["GoogleMapsURL"]
            match = re.search(r"@?([-]?\d+\.\d+),([-]?\d+\.\d+)", url)
            if match:
                lat = float(match.group(1))
                lon = float(match.group(2))
        else:
            lat = loc_conf["Latitude"]
            lon = loc_conf["Longitude"]

        if lat is None or lon is None:
            self.logger.log_message("Latitude and longitude could not be determined, using defaults for 0°00'00\"N 0°00'00.0\"E.", "warning")
            lat = 0.0
            lon = 0.0

        # Create location object and compute times
        location = LocationInfo(name=name, region="", timezone=tz, latitude=lat, longitude=lon)
        s = sun(location.observer, date=DateHelper.today(), tzinfo=pytz.timezone(tz))

        return {
            "dawn": s["dawn"].time(),
            "dusk": s["dusk"].time(),
        }

    def _map_schedules_to_outputs(self) -> dict:  # noqa: PLR0912
        """Map schedules to Shelly outputs based on the configuration.

        Returns:
            dict: A dictionary mapping Shelly output names to their assigned schedules, or an empty dictionary if no outputs are configured.
        """
        assignments = {}
        group_map = {}

        shelly_outputs = self.shelly_control.outputs
        if not isinstance(shelly_outputs, list) or len(shelly_outputs) == 0:
            self.logger.log_message("No Shelly outputs configured.", "warning")
            return assignments

        for output in shelly_outputs:
            name = output.get("Name")
            group = output.get("Group")
            if group:
                group_map.setdefault(group, []).append(name)
            assignments[name] = None

        lighting_controls = self.config.get("LightingControl", default=[])
        if not isinstance(lighting_controls, list) or len(lighting_controls) == 0:
            self.logger.log_message("No lighting controls configured.", "warning")
            return assignments

        for control in lighting_controls:
            target = control.get("Target")
            schedule = control.get("Schedule")
            ctrl_type = control.get("Type").lower()

            if ctrl_type == "default":
                continue
            elif ctrl_type == "switch":  # noqa: RET507
                if assignments.get(target):
                    self.logger.log_message(f"⚠️ Switch '{target}' already assigned to schedule '{assignments[target]}'", "warning")
                else:
                    assignments[target] = schedule
            elif ctrl_type == "switch group":
                for output in group_map.get(target, []):
                    if assignments.get(output):
                        self.logger.log_message(f"⚠️ Switch '{output}' (in group '{target}') already assigned to schedule '{assignments[output]}'", "warning")
                    else:
                        assignments[output] = schedule

        lighting_control_config = self.config.get("LightingControl", default=[])
        if not isinstance(lighting_control_config, list) or len(lighting_control_config) == 0:
            self.logger.log_fatal_error("No lighting control configurations found.")
        assert isinstance(lighting_control_config, list), "Lighting control configuration must be a list"

        default_schedule = next((c["Schedule"] for c in lighting_control_config if c.get("Type", "").lower() == "default"), None)
        for output, schedule in assignments.items():
            if schedule is None:
                assignments[output] = default_schedule

        return assignments

    def _load_state(self) -> dict:
        """Load the random offsets from the the saved state file.

        Returns:
            dict: The RandomOffsets section of the state file, or an empty dictionary if none exists.
        """
        assert isinstance(self.state_filepath, Path), "State file path must be a Path object"

        # If the file exists and its at least 500 bytes long
        if self.state_filepath.exists() and self.state_filepath.stat().st_size >= 500:
            try:
                with self.state_filepath.open("r", encoding="utf-8") as f:
                    state = json.load(f)

                    # Now load the RandomOffset cached values from the state file
                    self.offset_cache = state.get("RandomOffsets", {})

                    # Now load the SwitchEvents list from the state file
                    self.switch_events = state.get("SwitchEvents", [])
            except json.JSONDecodeError as e:
                self.logger.log_fatal_error(f"Failed to load state file {self.state_filepath}: {e}")
        return {}

    def _save_state(self):
        """Save the current state to the state file. This includes the RandomOffsets and SwitchStates sections."""
        # First trim any old switch events to keep only the last N days of history
        self._trim_switch_events()

        # Build the JSON state file and save it
        assert isinstance(self.state_filepath, Path), "State file path must be a Path object"
        try:  # noqa: PLR1702
            with self.state_filepath.open("w", encoding="utf-8") as f:
                config_schedules = self.config.get("Schedules", default=[])
                if isinstance(config_schedules, list):
                    schedules = copy.deepcopy(config_schedules)
                    # Convert the StartDate and EndDate in DatesOff to strings
                    for schedule in schedules:
                        for event in schedule.get("Events", []):
                            if "DatesOff" in event and isinstance(event["DatesOff"], list):
                                for rng in event.get("DatesOff", []):
                                    if "StartDate" in rng and "EndDate" in rng and isinstance(rng["StartDate"], dt.date) and isinstance(rng["EndDate"], dt.date):
                                        rng["StartDate"] = rng["StartDate"].isoformat()
                                        rng["EndDate"] = rng["EndDate"].isoformat()

                state_data = {
                    "StateFileType": "LightingControl",
                    "LastStateSaveTime": DateHelper.now_str(),
                    "DeviceType": "LightingController",
                    "DeviceName": self.config.get("General", "AppName", default="LightingControl"),
                    "LastStatusMessage": "State saved successfully.",
                    "Dawn": self.dusk_dawn.get("dawn").strftime("%H:%M"),  # type: ignore  # noqa: PGH003
                    "Dusk": self.dusk_dawn.get("dusk").strftime("%H:%M"),  # type: ignore  # noqa: PGH003
                    "RandomOffsets": self.offset_cache,
                    "SwitchStates": self.switch_states,
                    "Schedules": schedules,
                    "SwitchEvents": self.switch_events,
                }

                json.dump(state_data, f, indent=2)
        except (OSError, TypeError, ValueError) as e:
            self.logger.log_fatal_error(f"Failed to save state file: {e}")

        # Now if the WebsiteBaseURL has been set, save the state to the web server
        base_url = self.config.get("General", "WebsiteBaseURL", default=None)
        access_key = self.config.get("General", "WebsiteAccessKey")
        timeout_wait = self.config.get("General", "WebsiteTimeout", default=5)
        if base_url:
            api_url = base_url + "/api/submit"  # type: ignore[attr-defined]

            if access_key:
                api_url += f"?key={access_key}"  # Add access_key as a query parameter

            headers = {
                "Content-Type": "application/json",
            }
            json_object = state_data

            try:
                response = requests.post(api_url, headers=headers, json=json_object, timeout=timeout_wait)  # type: ignore[attr-defined]
                response.raise_for_status()
                self.logger.log_message(f"Posted LightingController state to {api_url}", "debug")
            except requests.exceptions.HTTPError as e:
                if response.status_code == HTTP_STATUS_FORBIDDEN:  # Handle 403 Forbidden error
                    self.logger.log_message(f"Access denied ({HTTP_STATUS_FORBIDDEN} Forbidden) when posting to {api_url}. Check your access key or permissions.", "error")
                else:
                    self.logger.log_message(f"HTTP error saving state to web server at {api_url}: {e}", "warning")
            except requests.exceptions.ConnectionError as e:  # Trap connection error - ConnectionError
                self.logger.log_message(f"Web server at {api_url} is unavailable. Error was: {e}", "warning")
            except requests.exceptions.RequestException as e:
                self.logger.log_fatal_error(f"Error saving state to web server at {api_url}: {e}")

    def _trim_switch_events(self):
        """Trim the switch events to keep only the last N days of history. Also sorts the events by date and time."""
        max_days = self.config.get("Files", "MaxDaysSwitchChangeHistory", default=30)
        assert isinstance(max_days, int), "MaxDaysSwitchChangeHistory must be an integer"
        assert max_days > 0, "MaxDaysSwitchChangeHistory must be a positive integer"

        cutoff_date = DateHelper.today_add_days(-max_days)
        cutoff_date_str = DateHelper.format_date(cutoff_date)

        # Filter out events older than the cutoff date
        self.switch_events = [day_event for day_event in self.switch_events if day_event["Date"] >= cutoff_date_str]
        # Now sort the switch events by date and time
        self.switch_events.sort(key=operator.itemgetter("Date"))
        for day_event in self.switch_events:
            day_event["Events"].sort(key=operator.itemgetter("Time"))

    @staticmethod
    def _generate_offset_key(schedule_name: str, event_index: int, mode: str) -> str:
        """Generate a unique key for the random offset based on schedule name, event index, and mode.

        Args:
            schedule_name (str): The name of the schedule.
            event_index (int): The index of the event in the schedule.
            mode (str): The mode, either "On" or "Off".

        Returns:
            str: A unique key formatted as "YYYY-MM-DD|ScheduleName|EventIndex|Mode".
        """
        today_str = DateHelper.today().isoformat()
        return f"{today_str}|{schedule_name}|{event_index}|{mode}"

    def evaluate_switch_states(self) -> list:
        """Evaluate the current switch states based on the schedules and the current time.

        Returns:
            list: A list of dictionaries containing the switch name, schedule name, and desired state.
        """
        now = DateHelper.now()
        weekday_str = WEEKDAY_ABBREVIATIONS[now.weekday()]
        self.switch_states.clear()

        if not self.schedule_map:
            self.logger.log_message("No schedules mapped to outputs.", "warning")
            return []

        for switch, schedule_name in self.schedule_map.items():
            schedule = self._get_schedule_by_name(schedule_name)
            if not schedule:
                self.logger.log_fatal_error(f"Schedule '{schedule_name}' not found for switch '{switch}'.")
                continue
            desired_state = self._evaluate_schedule(schedule, now, weekday_str)
            state_entry = {
                "Switch": switch,
                "Schedule": schedule_name,
                "State": desired_state
                }
            self.switch_states.append(state_entry)

        return self.switch_states

    def _get_schedule_by_name(self, name: str) -> dict | None:
        """Retrieve a schedule by its name from the configuration.

        Args:
            name (str): The name of the schedule to retrieve.

        Returns:
            dict: The schedule dictionary if found, or None if not found.
        """
        schedules = self.config.get("Schedules", default=[])
        if not isinstance(schedules, list):
            self.logger.log_message("No Schedules configured in the config file.", "warning")
            return None
        for schedule in schedules:
            if schedule.get("Name") == name:
                return schedule
        return None

    def _evaluate_schedule(self, schedule: dict, now: dt.datetime, weekday_str: str) -> str:
        """Evaluate a schedule to determine if the switch should be ON or OFF.

        Args:
            schedule (dict): The schedule to evaluate.
            now (datetime): The current datetime.
            weekday_str (str): The current weekday as a string.

        Returns:
            str: "ON" if the switch should be ON, "OFF" otherwise.
        """
        for idx, event in enumerate(schedule.get("Events", [])):
            days = event.get("DaysOfWeek", "All")
            if days != "All" and weekday_str not in [d.strip() for d in days.split(",")]:
                continue

            # Check if today falls within any specified DatesOff range which states that the switch should be OFF
            dates_off_list = event.get("DatesOff", [])
            if dates_off_list and len(dates_off_list) > 0:
                for rng in event.get("DatesOff", []):
                    try:
                        start = rng["StartDate"]
                        end = rng["EndDate"]
                        if start <= DateHelper.today() <= end:
                            return "OFF"
                    except (KeyError, TypeError) as e:
                        self.logger.log_message(f"Invalid StartDate and/or EndDate in the DatesOff range: {rng} — {e}", "error")
                        continue

            on_time = self._parse_time(event["TurnOn"], event.get("RandomOffset"), schedule["Name"], idx, "On")
            off_time = self._parse_time(event["TurnOff"], event.get("RandomOffset"), schedule["Name"], idx, "Off")

            if on_time is None or off_time is None:
                continue

            self.logger.log_message(f"Schedule '{schedule['Name']}' as at {now.time().strftime('%H:%M')} evaluates as: On: {on_time.strftime('%H:%M')}, Off: {off_time.strftime('%H:%M')}", "debug")
            if on_time < off_time:
                if on_time <= now.time() < off_time:
                    return "ON"
            elif now.time() >= on_time or now.time() < off_time:
                return "ON"

        return "OFF"

    def change_switch_states(self):
        """Change the switch states based on the evaluated switch states.

        This method will iterate through the evaluated switch states and change the state of each switch accordingly.
        """
        # First refresh the status of all devices
        try:
            self.shelly_control.refresh_all_device_statuses()
        except RuntimeError as e:
            self.logger.log_message(f"Failed to refresh device statuses: {e}", "error")
            return

        for state in self.switch_states:
            switch = state["Switch"]
            switch_component = self.shelly_control.get_device_component("output", switch)
            assert switch_component is not None, f"Output component '{switch}' not found in Shelly outputs"
            current_state = "ON" if switch_component.get("State") else "OFF"
            desired_state = state["State"]
            if desired_state != current_state:
                self.shelly_control.change_output(switch_component, desired_state == "ON")
                self.logger.log_message(f"Changing state of switch '{switch}' from {current_state} to {desired_state}", "detailed")
                self._record_switch_event(switch, state["Schedule"], desired_state)

    def _record_switch_event(self, switch, schedule, state):
        """Record a switch change event.

        Args:
            switch (str): The name of the switch.
            schedule (str): The name of the schedule.
            state (str): The new state of the switch.
        """
        # Example of switch_events structure:
        """
        self.switch_events = [
            {
                "Date": "2025-08-01",
                "Events": [
                    {
                        "Time": "12:13",
                        "Switch": "Switch 01",
                        "Schedule": "Schedule name",
                        "State": "ON"
                    },
                    {
                        "Time": "15:00",
                        "Switch": "Switch 01",
                        "Schedule": "Schedule name",
                        "State": "OFF"
                    }
                ]
            },
            {
                "Date": "2025-08-02",
                "Events": [
                    {
                        "Time": "12:10",
                        "Switch": "Switch 01",
                        "Schedule": "Schedule name",
                        "State": "ON"
                    },
                    {
                        "Time": "16:13",
                        "Switch": "Switch 01",
                        "Schedule": "Schedule name",
                        "State": "OFF"
                    }
                ]
             }
        ]
        """

        event_date = DateHelper.today_str()
        # Ensure the switch_events list has an entry for today somewhere in the list
        for day_event in self.switch_events:
            if day_event["Date"] == event_date:
                # If we found today's date, append the event to today's events
                day_event["Events"].append({
                    "Time": DateHelper.now_str(datetime_format="%H:%M:%S"),
                    "Switch": switch,
                    "Schedule": schedule,
                    "State": state
                })
                return

        # If we didn't find today's date, create a new entry
        event = {
            "Date": event_date,
            "Events": [
                {
                    "Time": DateHelper.now_str(datetime_format="%H:%M:%S"),
                    "Switch": switch,
                    "Schedule": schedule,
                    "State": state
                }
            ]
        }
        self.switch_events.append(event)

    def _parse_time(self, time_str, offset_minutes, schedule_name, event_index, mode) -> dt.time:
        """Parse a time string and apply any random offset if specified. Exits if the time string is invalid.

        The time stings are found in the TurnOn and TurnOff fields of the schedule events in the config file and can be any of these types:
        - "HH:MM" format (e.g., "14:30")
        - "dawn" or "dusk" with optional hh:mm offset (e.g., "dawn+00:10" or "dusk-01:30")

        Args:
            time_str (str): The time string to parse, can be in "HH:MM" format or "dawn" / "dusk" with optional offset.
            offset_minutes (int): The maximum number of minutes to offset the time randomly.
            schedule_name (str): The name of the schedule for logging.
            event_index (int): The index of the event in the schedule.
            mode (str): The mode, either "On" or "Off".

        Returns:
            time: The parsed time with any random offset applied.
        """
        local_tz = dt.datetime.now().astimezone().tzinfo

        if not self.dusk_dawn:
            self.logger.log_fatal_error(f"Dawn/Dusk times have not been set for schedule '{schedule_name}', event {event_index} ({mode})")

        # Check for dawn/dusk with optional offset
        if time_str.lower().startswith(("dawn", "dusk")):
            # Extract base time type and any offset
            if time_str.lower().startswith("dawn"):
                base_time = self.dusk_dawn["dawn"]
                offset_part = time_str[4:]  # Everything after "dawn"
            else:  # dusk
                base_time = self.dusk_dawn["dusk"]
                offset_part = time_str[4:]  # Everything after "dusk"

            # Parse any dawn/dusk time offset (e.g., "+00:10" or "-01:30")
            if offset_part:
                try:
                    # Match pattern like "+00:10" or "-01:30"
                    match = re.match(r"^([+-])(\d{2}):(\d{2})$", offset_part)
                    if match:
                        sign, hours, minutes = match.groups()
                        total_minutes = int(hours) * 60 + int(minutes)
                        if sign == "-":
                            total_minutes = -total_minutes

                        # Apply the offset to base_time
                        base_datetime = dt.datetime.combine(DateHelper.today(), base_time)
                        adjusted_datetime = base_datetime + dt.timedelta(minutes=total_minutes)
                        base_time = adjusted_datetime.time()
                    else:
                        self.logger.log_fatal_error(f"Invalid dawn/dusk offset format for the schedule '{schedule_name}', time entry '{time_str}'. Use format like 'Dawn+00:10' or 'Dusk-01:30'")
                except (ValueError, TypeError, OSError):
                    self.logger.log_fatal_error(f"Invalid dawn/dusk offset format for the schedule '{schedule_name}', time entry '{time_str}'. Use format like 'Dawn+00:10' or 'Dusk-01:30'")
        else:
            try:
                base_time = dt.datetime.strptime(time_str, "%H:%M").replace(tzinfo=local_tz).time()
            except ValueError:
                self.logger.log_fatal_error(f"Invalid time format for the schedule '{schedule_name}', time entry '{time_str}'. Use format like 'HH:MM'")

        if offset_minutes:
            key = self._generate_offset_key(schedule_name, event_index, mode)
            if key not in self.offset_cache:
                delta = random.randint(-offset_minutes, offset_minutes)
                self.offset_cache[key] = delta
            return (dt.datetime.combine(DateHelper.today(), base_time) + dt.timedelta(minutes=self.offset_cache[key])).time()
            # return dt.datetime.strptime(offset_time, "%H:%M:%S").replace(tzinfo=local_tz).time()
        return base_time

    def ping_heatbeat(self, is_fail: bool | None = None) -> bool:  # noqa: FBT001
        """Ping the heartbeat URL to check if the service is available.

        Args:
            is_fail (bool, optional): If True, the heartbeat will be considered a failure.

        Returns:
            bool: True if the heartbeat URL is reachable, False otherwise.
        """
        heartbeat_url = self.config.get("HeartbeatMonitor", "WebsiteURL")
        timeout = self.config.get("HeartbeatMonitor", "HeartbeatTimeout", default=10)

        if heartbeat_url is None:
            self.logger.log_message("Heartbeat URL not configured - skipping sending a heatbeat.", "debug")
            return True
        assert isinstance(heartbeat_url, str), "Heartbeat URL must be a string"

        if is_fail:
            heartbeat_url += "/fail"

        try:
            response = requests.get(heartbeat_url, timeout=timeout)  # type: ignore[call-arg]
        except requests.exceptions.Timeout as e:
            self.logger.log_message(f"Timeout making Heartbeat ping: {e}", "error")
            return False
        except requests.RequestException as e:
            self.logger.log_message(f"Heartbeat ping failed: {e}", "error")
            return False
        else:
            if response.status_code == 200:
                self.logger.log_message("Heartbeat ping successful.", "debug")
                return True
            self.logger.log_message(f"Heartbeat ping failed with status code: {response.status_code}", "error")
            return False

    def run(self):
        """Run the controller, continuously evaluating switch states and checking for configuration changes."""
        assert isinstance(self.check_interval, int), "CheckInterval must be an integer"
        assert self.check_interval > 0, "CheckInterval must be a positive integer"

        while True:
            if self.config.check_for_config_changes():
                self.logger.log_message("Configuration file changed, reloading...", "detailed")
                self._initialise()

            self.evaluate_switch_states()
            self.change_switch_states()
            self._save_state()  # Save the latest state to the file including any switch change events
            time.sleep(self.check_interval)
            self.ping_heatbeat()
