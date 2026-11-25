"""Helpers for BYD remote-control entities.

Kept free of Home Assistant imports so tests can load this module.
"""

from __future__ import annotations

from typing import Any

from .pybyd_china.models.realtime import (
    DoorOpenState,
    SeatHeatVentState,
    StearingWheelHeat,
    WindowState,
)

SIDE_WINDOW_ATTRS: tuple[str, ...] = (
    "left_front_window",
    "right_front_window",
    "left_rear_window",
    "right_rear_window",
)

SEAT_PARAM_ATTRS: tuple[tuple[str, str], ...] = (
    ("main_seat_heat_state", "mainHeat"),
    ("main_seat_ventilation_state", "mainVentilation"),
    ("copilot_seat_heat_state", "copilotHeat"),
    ("copilot_seat_ventilation_state", "copilotVentilation"),
    ("lr_seat_heat_state", "lrSeatHeatState"),
    ("lr_seat_ventilation_state", "lrSeatVentilationState"),
    ("rr_seat_heat_state", "rrSeatHeatState"),
    ("rr_seat_ventilation_state", "rrSeatVentilationState"),
)


def _enum_int(value: Any) -> int | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def windows_are_closed(realtime: Any | None) -> bool | None:
    """Return True when all known side windows are closed."""
    if realtime is None:
        return None
    known: list[bool] = []
    for attr in SIDE_WINDOW_ATTRS:
        state = getattr(realtime, attr, None)
        if state is None:
            continue
        if state == WindowState.OPEN or _enum_int(state) == int(WindowState.OPEN):
            return False
        if state == WindowState.CLOSED or _enum_int(state) == int(WindowState.CLOSED):
            known.append(True)
    return True if known else None


def trunk_is_closed(realtime: Any | None) -> bool | None:
    """Return True when the trunk lid is closed."""
    if realtime is None:
        return None
    state = getattr(realtime, "trunk_lid", None)
    if state is None:
        return None
    if state == DoorOpenState.OPEN or _enum_int(state) == int(DoorOpenState.OPEN):
        return False
    if state == DoorOpenState.CLOSED or _enum_int(state) == int(DoorOpenState.CLOSED):
        return True
    return None


def _pick_attr(hvac: Any | None, realtime: Any | None, attr: str) -> Any | None:
    for source in (hvac, realtime):
        if source is None:
            continue
        value = getattr(source, attr, None)
        if value is not None:
            return value
    return None


def _seat_command_level(value: Any) -> int:
    if value is None:
        return 0
    if hasattr(value, "to_command_level"):
        try:
            return int(value.to_command_level())
        except (TypeError, ValueError):
            return 0
    raw = _enum_int(value)
    if raw is None:
        return 0
    try:
        return int(SeatHeatVentState(raw).to_command_level())
    except ValueError:
        return 0


def _wheel_command_level(value: Any) -> int:
    if value is None:
        return int(StearingWheelHeat.OFF.to_command_level())
    if hasattr(value, "to_command_level"):
        try:
            return int(value.to_command_level())
        except (TypeError, ValueError):
            return int(StearingWheelHeat.OFF.to_command_level())
    raw = _enum_int(value)
    if raw is None:
        return int(StearingWheelHeat.OFF.to_command_level())
    try:
        return int(StearingWheelHeat(raw).to_command_level())
    except ValueError:
        return int(StearingWheelHeat.OFF.to_command_level())


def build_seat_climate_params(
    hvac: Any | None = None,
    realtime: Any | None = None,
    *,
    steering_on: bool | None = None,
) -> dict[str, Any]:
    """Build VENTILATIONHEATING controlParamsMap payload."""
    params: dict[str, Any] = {
        "remoteMode": 1,
        "chairType": "5",
    }
    for attr, key in SEAT_PARAM_ATTRS:
        params[key] = _seat_command_level(_pick_attr(hvac, realtime, attr))
    if steering_on is True:
        params["steeringWheelHeatState"] = int(StearingWheelHeat.ON.to_command_level())
    elif steering_on is False:
        params["steeringWheelHeatState"] = int(StearingWheelHeat.OFF.to_command_level())
    else:
        params["steeringWheelHeatState"] = _wheel_command_level(
            _pick_attr(hvac, realtime, "steering_wheel_heat_state")
        )
    return params


def build_battery_heat_params(on: bool) -> dict[str, int]:
    """Build BATTERYHEAT controlParamsMap payload."""
    return {"batteryHeatSwitch": 1 if on else 0}


def infer_hvac_mode(is_on: bool, interior: float | None, target: float | None) -> str:
    """Return HA HVAC mode name for Bemfa-friendly climate reporting."""
    if not is_on:
        return "off"
    if interior is not None and target is not None:
        if target > interior + 1:
            return "heat"
        if target < interior - 1:
            return "cool"
    return "cool"
