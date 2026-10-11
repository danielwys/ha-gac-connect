"""Human-readable equipment explanations, without telemetry or identifiers."""
from ._vendor.gac_connect.capabilities import COMMAND_FEATURES
from .capabilities import ENTITY_FEATURES, FRIDGE_ENTITIES
from .const import CONF_ENABLE_FRIDGE, CONF_ENABLE_TRACKER, CONF_OMIT_ENTITIES, DEFAULT_ENABLE_TRACKER
from .entity_filter import OMIT_OPTIONS, is_aion_ut, model_omissions


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
        elif key in FRIDGE_ENTITIES:
            explanation = ("Shown only when fridge readings are available." if entry.options.get(CONF_ENABLE_FRIDGE, True)
                           else "Hidden: fridge controls are turned off.")
        elif key == "device_tracker:location" and not entry.options.get(CONF_ENABLE_TRACKER, DEFAULT_ENABLE_TRACKER):
            explanation = "Hidden: location tracking is turned off."
        elif key == "button:charge_pause":
            explanation = "Unavailable: continuous charging pause is not supported."
        else:
            explanation = "Included by the selected profile; support has not been verified by Auto detect."
        rows.append(f"- **{label}** — {explanation}")
    if manager is None:
        rows.insert(0, "Auto detect evidence is available only while that profile is loaded.\n")
    return "\n".join(rows)
