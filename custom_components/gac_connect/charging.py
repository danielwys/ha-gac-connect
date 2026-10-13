"""Present reported charging information without inventing clock conversions."""
from ._vendor.gac_connect.models import ChargingMode

DAYS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
        "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}


def reported_days(mask: int | None) -> str:
    if mask is None:
        return "Not reported"
    if mask in (0, 127):
        return "Every day"
    return ", ".join(day for index, day in enumerate(DAYS.values()) if mask & (1 << index))


SCHEDULE_TYPES = {ChargingMode.FREE: "Immediately", ChargingMode.SCHEDULED: "Once", ChargingMode.LOOP: "Repeating"}


def schedule_mode(status) -> str:
    if status is None:
        return "not_reported"
    return {ChargingMode.FREE: "unrestricted", ChargingMode.SCHEDULED: "scheduled",
            ChargingMode.LOOP: "scheduled"}.get(status.charging_mode, "not_reported")


def schedule_attributes(status) -> dict:
    return {
        "reported_start": status.charge_window_start if status else None,
        "reported_stop": status.charge_window_stop if status else None,
        "reported_days": reported_days(status.charge_weekly if status else None),
        "reported_schedule_type": SCHEDULE_TYPES.get(getattr(status, "charging_mode", None), "Not reported"),
        "reported_clock_verified": False,
        "reported_clock_consistent": getattr(status, "charge_clock_consistent", None),
        "reported_time_format": getattr(status, "charge_time_format", None),
        "stopping_rule": {0: "Stop time", 1: "Vehicle target charge level"}.get(
            getattr(status, "charge_end_type", None), "Not reported"),
    }


def schedule_summary(coordinator) -> str:
    data = coordinator.data
    mode = {"unrestricted": "Unrestricted charging", "scheduled": "Scheduled charging",
            "not_reported": "Not reported"}[schedule_mode(data)]
    if schedule_mode(data) == "scheduled":
        mode += f" ({schedule_attributes(data)['reported_schedule_type'].lower()})"
    fields = schedule_attributes(data)
    age = coordinator.report_age
    age_text = "Not reported" if age is None else "Clock ahead" if age < -60 else f"{max(0, int(age // 60))} minutes"
    refresh = {"waiting": "No refresh yet", "ok": "Last request succeeded", "quiet_hours": "Quiet hours",
               "rate_limited": "Waiting for request limit", "error": "Last refresh failed",
               "auth_required": "Sign in again"}[coordinator.polling_state]
    clock_note = ("The calendar fields and timestamps disagree in the vehicle's reported time zone. "
                  "Calendar start/stop fields are shown; verify them in the official app. "
                  if fields["reported_clock_consistent"] is False else
                  "The reported clock has not been independently verified against the scheduling time zone. ")
    return (f"**Reported mode:** {mode}\n\n"
            f"**Reported window:** {fields['reported_start'] or 'Not reported'} → "
            f"{fields['reported_stop'] or 'Not reported'}\n\n"
            f"**Reported days:** {fields['reported_days']}\n\n"
            f"**Stopping rule:** {fields['stopping_rule']}\n\n"
            f"**Report age:** {age_text} · {refresh}\n\n"
            f"**Scheduling time zone:** {coordinator.client.timezone}\n\n"
            "These are cached vehicle readings. Use the Last report and Data freshness sensors to judge their age. "
            "A saved window may still be reported in unrestricted mode; that does not mean it is active. "
            f"{clock_note}The target charge level is not available here.")


def window_target(start: str, stop: str, weekly: int, until_soc: bool) -> dict:
    """The reported fields required to confirm a requested window."""
    return {"charge_scheduled": True, "charge_window_start": start,
            "charge_window_stop": stop, "charge_weekly": weekly, "charge_end_type": int(until_soc)}
