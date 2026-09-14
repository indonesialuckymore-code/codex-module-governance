"""Shared defaults and evidence checks; the platform owns model availability."""
import re

DEFAULT_TASK_MODEL = "gpt-5.6-terra"
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
LEGACY_MANUAL_METHOD = "MANUAL_UI_TERRA_SELECTION_EVIDENCE"
MANUAL_MODEL_METHOD = "MANUAL_UI_MODEL_SELECTION_EVIDENCE"
READBACK_MODEL_METHOD = "RUNTIME_MODEL_READBACK"
WINDOW_MODEL_METHODS = {
    "NATIVE_CREATE_THREAD_MODEL_PARAMETER", "NATIVE_SEND_MESSAGE_MODEL_OVERRIDE",
    LEGACY_MANUAL_METHOD, MANUAL_MODEL_METHOD, READBACK_MODEL_METHOD,
}
FULL_ACCESS_PROFILES = {":danger-full-access", "danger-full-access", "full-access", "disabled"}


def valid_model(value):
    return (isinstance(value, str) and MODEL_ID.fullmatch(value) is not None
            and value.upper() not in {"UNKNOWN", "UNVERIFIED", "DEFAULT", "NONE"})


def runtime_model_is_recorded(record):
    control = record.get("modelEnforcement")
    if not isinstance(control, dict) or not valid_model(record.get("model")):
        return False
    return (
        record["model"] == record.get("runtimeModel") == control.get("model")
        and control.get("method") in WINDOW_MODEL_METHODS
        and isinstance(control.get("evidenceRef"), str)
        and MODEL_ID.fullmatch(control["evidenceRef"]) is not None
        and (control["method"] != LEGACY_MANUAL_METHOD or record["model"] == DEFAULT_TASK_MODEL)
    )
