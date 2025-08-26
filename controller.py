import copy
import datetime as dt
import json
import operator
import random
import re
import threading
from pathlib import Path

import pytz
import requests
from astral import LocationInfo
from astral.sun import sun
from sc_utility import DateHelper, SCCommon, SCConfigManager, SCLogger, ShellyControl

from post_state_to_web_server import post_state_to_web_server

WEEKDAY_ABBREVIATIONS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class LightingController:
    def __init__(self, config: SCConfigManager, logger: SCLogger):
        self.config = config
        self.logger = logger
        self.dusk_dawn = {}
        self.schedule_map = {}
        self.input_map = {}  # A map if inputs to outputs
        self.state_filepath = None
        self.offset_cache = {}
        self.switch_states = []  # The current state of each switch
        self.switch_events = []  # List of switch change events for logging purposes. This is a list of days, and for each day, a list of events with the time, switch identifier and state change.
        self.config_last_check = DateHelper.now()  # Last time the config file was checked for changes
        self.wake_event = threading.Event()

        # Initialize the ShellyControl class
        # Create an instance of the ShellyControl class
        shelly_settings = config.get_shelly_settings()
        if shelly_settings is None:
            logger.log_fatal_error("No Shelly settings found in the configuration file.")
            return
        try:
            assert isinstance(shelly_settings, dict)
            self.shelly_control = ShellyControl(logger, shelly_settings, self.wake_event)
        except RuntimeError as e:
            logger.log_fatal_error(f"Shelly control initialization error: {e}")
            return

        self._initialise()

        self.evaluate_switch_states()   # Build the switch_states list

    def _initialise(self):
        """Initialise the controller, refreshing the state and config as needed."""
        self.dusk_dawn = self.get_dusk_dawn_times()
        self.schedule_map = self._map_schedules_to_outputs()
        self.input_map = self._map_inputs_to_outputs()
        state_file = self.config.get("Files", "SavedStateFile", default="system_state.json")
        self.state_filepath = SCCommon.select_file_location(state_file)  # type: ignore[attr-defined]
        self._load_state()

        self.check_interval = self.config.get("General", "CheckInterval", default=5) or 5

        self.logger.log_message("Lighting Controller initialised successfully.", "debug")

    def get_dusk_dawn_times(self) -> dict:
        """Get the dawn and dusk times based on the location returned from the specified shelly switch or the manually configured location configuration.

        Returns:
            dict: A dictionary with 'dawn' and 'dusk' times.
        """
        name = "LightingControl"
        loc_conf = self.config.get("Location", default={})
        assert isinstance(loc_conf, dict), "Location configuration must be a dictionary"
        tz = lat = lon = None

        shelly_device_name = loc_conf.get("UseShellyDevice")
        if shelly_device_name:
            # Get the tz, lat and long from the specified Shelly device
            try:
                device = self.shelly_control.get_device(shelly_device_name)
                shelly_loc = self.shelly_control.get_device_location(device)
                if shelly_loc:
                    tz = shelly_loc.get("tz")
                    lat = shelly_loc.get("lat")
                    lon = shelly_loc.get("lon")
            except (RuntimeError, TimeoutError) as e:
                self.logger.log_message(f"Error getting location from Shelly device {shelly_device_name}: {e}", "warning")

        # If we were unable to get the location from the Shelly device, see if we can extract it from the Google Maps url (if supplied)
        if tz is None:
            tz = loc_conf["Timezone"]
            # Extract coordinates
            if "GoogleMapsURL" in loc_conf and loc_conf["GoogleMapsURL"] is not None:
                url = loc_conf["GoogleMapsURL"]
                match = re.search(r"@?([-]?\d+\.\d+),([-]?\d+\.\d+)", url)
                if match:
                    lat = float(match.group(1))
                    lon = float(match.group(2))
            else:   # Last resort, try the config values
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

    def _map_inputs_to_outputs(self) -> dict:  # noqa: PLR0912
        """
        Map Shelly outputs to their associated inputs based on the InputControls section of the configuration.

        Returns:
            dict: A dictionary mapping output names to their assigned input names, or None if no input is mapped.
        """
        assignments = {}
        group_map = {}

        shelly_outputs = self.shelly_control.outputs
        if not isinstance(shelly_outputs, list) or len(shelly_outputs) == 0:
            self.logger.log_message("No Shelly outputs configured.", "warning")
            return assignments

        # Build group map for outputs
        for output in shelly_outputs:
            name = output.get("Name")
            group = output.get("Group")
            if group:
                group_map.setdefault(group, []).append(name)
            assignments[name] = None

        input_controls = self.config.get("InputControls", default=[])
        if not isinstance(input_controls, list) or len(input_controls) == 0:
            self.logger.log_message("No input controls configured.", "warning")
            return assignments

        default_input = None
        for control in input_controls:
            ctrl_type = control.get("Type", "").lower()
            target = control.get("Target")
            input_name = control.get("Input")

            if ctrl_type == "default":
                default_input = input_name
                continue
            if ctrl_type == "switch":
                if assignments.get(target):
                    self.logger.log_message(f"⚠️ Output '{target}' already assigned to input '{assignments[target]}'", "warning")
                else:
                    assignments[target] = input_name
            elif ctrl_type == "switch group":
                for output in group_map.get(target, []):
                    if assignments.get(output):
                        self.logger.log_message(f"⚠️ Output '{output}' (in group '{target}') already assigned to input '{assignments[output]}'", "warning")
                    else:
                        assignments[output] = input_name

        # Map all unmapped outputs to the default input if specified
        if default_input:
            for output, mapped_input in assignments.items():
                if mapped_input is None:
                    assignments[output] = default_input

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
        post_state_to_web_server(self.config, self.logger, state_data)

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
                "State": desired_state,
                "Input": self.input_map.get(switch),
                "InputState": None,     # Thsi will get set by the change_swutch_states() method
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
            output_control = state["Switch"]
            input_control = state["Input"]

            # See what the current state of the outputs is
            switch_component = self.shelly_control.get_device_component("output", output_control)
            assert switch_component is not None, f"Output component '{output_control}' not found in Shelly outputs"
            if self.shelly_control.is_device_online(switch_component):
                current_output_state = "ON" if switch_component.get("State") else "OFF"

                # If the switch has an associated input control, we need to check its state as well
                if input_control:
                    input_component = self.shelly_control.get_device_component("input", input_control)
                    input_state = "ON" if input_component.get("State") else "OFF"
                    state["InputState"] = input_state  # Store the input state in the state entry

                # First see if we have an override by the input control
                if input_control and input_state == "ON":
                    if current_output_state == "OFF":
                        self.logger.log_message(f"Input '{input_control}' is ON, overriding switch '{output_control}' to ON", "detailed")
                        self.shelly_control.change_output(switch_component, True)
                        self._record_switch_event(switch=output_control, state="ON", input_name=input_control)
                    state["State"] = "ON"
                    continue

                # Otherwise see if we need to turn on or off based on the schedule
                scheduled_state = state["State"]
                if scheduled_state != current_output_state:
                    self.shelly_control.change_output(switch_component, scheduled_state == "ON")
                    self.logger.log_message(f"Changing state of switch '{output_control}' from {current_output_state} to {scheduled_state} due to schedule {state['Schedule']}", "detailed")
                    self._record_switch_event(switch=output_control, state=scheduled_state, schedule_name=state["Schedule"])
                    state["State"] = scheduled_state
            else:
                self.logger.log_message(f"Switch '{output_control}' is offline, skipping state change", "debug")

    def _record_switch_event(self, switch: str, state: str, schedule_name: str | None = None, input_name: str | None = None):
        """Record a switch change event. You must supply either a schedule or an input.

        Args:
            switch (str): The name of the switch.
            state (str): The new state of the switch.
            schedule_name (str): The name of the schedule (if any)
            input_name (str): The name of the input control (if any).
        """
        event_date = DateHelper.today_str()
        # Ensure the switch_events list has an entry for today somewhere in the list
        for day_event in self.switch_events:
            if day_event["Date"] == event_date:
                # If we found today's date, append the event to today's events
                day_event["Events"].append({
                    "Time": DateHelper.now_str(datetime_format="%H:%M:%S"),
                    "Switch": switch,
                    "Schedule": schedule_name,
                    "Input": input_name,
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
                    "Schedule": schedule_name,
                    "Input": input_name,
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
            config_timestamp = self.config.check_for_config_changes(self.config_last_check)
            if config_timestamp:
                self.reload_config()

            self.evaluate_switch_states()
            self.change_switch_states()
            self._save_state()  # Save the latest state to the file including any switch change events
            self.wake_event.wait(timeout=self.check_interval)
            # Clear the event so future waits block again
            if self.wake_event.is_set():

                # We were woken by a webhook call
                event = self.shelly_control.pull_webhook_event()
                if event:
                    self.logger.log_message(f"Webhook event received {event.get('Event')} from component {event.get('Component').get('Name')}", "debug")  # pyright: ignore[reportOptionalMemberAccess]

                self.wake_event.clear()
            self.ping_heatbeat()

    def reload_config(self):
        """Apply the updated configureation settings ."""
        self.logger.log_message("Reloading configuration...", "detailed")

        try:
            # First update the logger
            logger_settings = self.config.get_logger_settings()
            self.logger.initialise_settings(logger_settings)

            # Then email settings
            email_settings = self.config.get_email_settings()
            if email_settings:
                self.logger.register_email_settings(email_settings)

            # And finally reinitialise the shelly switches
            shelly_settings = self.config.get_shelly_settings()
            if shelly_settings is None:
                self.logger.log_fatal_error("No Shelly settings found in the configuration file.")
                return
            # assert isinstance(shelly_settings, dict)
            self.shelly_control.initialize_settings(device_settings=shelly_settings, refresh_status=True)

        except RuntimeError as e:
            self.logger.log_fatal_error(f"Error reloading and applying configuration changes: {e}")
            return
        else:
            # Finally, re-initialise ourselves
            self._initialise()
            self.config_last_check = DateHelper.now()
