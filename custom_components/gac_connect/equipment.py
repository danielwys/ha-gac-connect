"""Human-readable equipment explanations, without telemetry or identifiers."""
from ._vendor.gac_connect.capabilities import COMMAND_FEATURES
from .capabilities import ENTITY_FEATURES, FRIDGE_ENTITIES
from .const import (
    CONF_ALLOW_VEHICLE_SHUTDOWN, CONF_ENABLE_FRIDGE, CONF_ENABLE_TRACKER,
    CONF_OMIT_ENTITIES, DEFAULT_ENABLE_TRACKER,
)
from .entity_filter import LOCATION_ENTITIES, OMIT_OPTIONS, is_aion_ut, model_omissions


def equipment_summary(entry) -> str:
    runtime = getattr(entry, "runtime_data", None)
    manager = getattr(getattr(runtime, "coordinator", None), "capabilities", None)
    rows = []
    forced = model_omissions(entry)
    for key, label in OMIT_OPTIONS.items():
        if manager is not None:
            explanation = manager.explanation(key)
            feature = ENTITY_FEATURES.get(key)
            commands = [name for name, equipment in COMMAND_FEATURES.items() if equipment == feature]
            if commands:
                confirmed = [name for name in commands if manager.detector.command(name).source == "confirmed_command"]
                explanation += " Successful command results: " + (", ".join(confirmed) if confirmed else "none yet") + "."
        elif key in forced or (key in FRIDGE_ENTITIES and is_aion_ut(entry)):
            explanation = "Omitted by the selected model profile."
        elif key in entry.options.get(CONF_OMIT_ENTITIES, ()):
            explanation = "Hidden by your Omit choice."
        elif key == "button:vehicle_power_off":
            explanation = ("Enabled in Vehicle settings." if entry.options.get(CONF_ALLOW_VEHICLE_SHUTDOWN) is True
                           else "Hidden: remote vehicle shutdown is disabled in Vehicle settings.")
        elif key in FRIDGE_ENTITIES:
            explanation = ("Shown only when fridge readings are available." if entry.options.get(CONF_ENABLE_FRIDGE, True)
                           else "Hidden: fridge controls are turned off.")
        elif key in LOCATION_ENTITIES and not entry.options.get(CONF_ENABLE_TRACKER, DEFAULT_ENABLE_TRACKER):
            explanation = "Hidden: location tracking is turned off."
        elif key == "button:charge_pause":
            explanation = "Unavailable: continuous charging pause is not supported."
        else:
            explanation = "Included by the selected profile; support has not been verified by Auto detect."
        rows.append(f"- **{label}** — {explanation}")
    if manager is None:
        rows.insert(0, "Auto detect evidence is available only while that profile is loaded.\n")
    rows.append("\nDriver and front passenger seat ventilation each have a level select (off, low, medium, high); "
                "the console's Smart mode is not reported, and choosing a level replaces it. "
                "Vehicle power shows on/off from the reported power mode, independently of Online and A/C. "
                "Turn vehicle off sends the app's shutdown request; remote power-on is not implemented. "
                "Seat status, A/C auto/compressor and anti-theft entities show the vehicle's report. "
                "Climate On preserves the fan and outlets; its fan selector offers Off and speeds 1–7. "
                "Console-only speeds 8–10 remain visible in Cabin fan strength. "
                "A speed selection starts or renews the timed A/C run at the selected temperature. "
                "Climate airflow offers cabin, cabin + feet, feet and feet + windscreen when Vehicle power is On. "
                "Recirculation shows the reported air source (on recirculates, off draws fresh air; the console's "
                "Auto reports as fresh air) and can be changed only while A/C runs. "
                "Battery preconditioning is on while the car reports a requested or scheduled run; off cancels it. "
                "Windscreen-only was rejected by the cloud; all-outlets is not verified remotely. "
                "Raw seat enableWind is available for comparing console On and Smart modes. "
                "The Climate heating/cooling presets use the app's automatic A/C temperature extremes. "
                "The Ventilation switch controls cabin ventilation, not the seats. "
                "Phone accessor records are metadata, not exposed calling controls.")
    return "\n".join(rows)
