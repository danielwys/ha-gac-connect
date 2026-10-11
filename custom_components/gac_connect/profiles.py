"""Equipment profile selection, including entries created before profiles were stored."""
from .const import CONF_MODEL, CONF_PROFILE, PROFILE_AUTO, PROFILE_CUSTOM, PROFILE_UT, PROFILE_V


def profile_for(entry) -> str:
    selected = entry.options.get(CONF_PROFILE)
    if selected in (PROFILE_AUTO, PROFILE_CUSTOM, PROFILE_UT, PROFILE_V):
        return selected
    model = str(entry.data.get(CONF_MODEL) or "").strip().casefold()
    if model == "aion ut" or model.startswith("aion ut "):
        return PROFILE_UT
    if model == "aion v" or model.startswith("aion v "):
        return PROFILE_V
    return PROFILE_CUSTOM
