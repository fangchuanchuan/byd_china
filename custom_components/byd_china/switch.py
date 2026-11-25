"""Switch entities for BYD remote actions.

Find-car, flash, battery heat and steering-wheel heat stay on the same
vehicle device as the rest of the car.
"""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from .pybyd_china.models.vehicle import Vehicle

from .const import DOMAIN
from .coordinator import BydDataUpdateCoordinator
from .entity import BydVehicleEntity
from .pybyd_china.models.realtime import StearingWheelHeat
from .remote import build_battery_heat_params, build_seat_climate_params

_LOGGER = logging.getLogger(__name__)

_OPTIMISTIC_TTL = 30.0
_PULSE_SECONDS = 5.0


@dataclass(frozen=True, kw_only=True)
class BydSwitchDescription(SwitchEntityDescription):
    command_on: str
    command_off: str | None = None
    pulse: bool = False
    params_on: Callable[[Any], dict[str, Any] | None] | None = None
    params_off: Callable[[Any], dict[str, Any] | None] | None = None
    is_on_fn: Callable[[Any], bool | None] | None = None


def _snapshot_is_battery_heating(snap: Any) -> bool | None:
    realtime = getattr(snap, "realtime", None) if snap is not None else None
    if realtime is None:
        return None
    return realtime.is_battery_heating


def _snapshot_is_steering_heating(snap: Any) -> bool | None:
    if snap is None:
        return None
    for source in (getattr(snap, "hvac", None), getattr(snap, "realtime", None)):
        if source is None:
            continue
        prop = getattr(source, "is_steering_wheel_heating", None)
        if callable(prop):
            try:
                value = prop()
            except TypeError:
                value = None
            if value is not None:
                return bool(value)
        state = getattr(source, "steering_wheel_heat_state", None)
        if state is None:
            continue
        if state == StearingWheelHeat.ON:
            return True
        if state == StearingWheelHeat.OFF:
            return False
    return None


def _battery_params_on(_snap: Any) -> dict[str, Any]:
    return build_battery_heat_params(True)


def _battery_params_off(_snap: Any) -> dict[str, Any]:
    return build_battery_heat_params(False)


def _steering_params_on(snap: Any) -> dict[str, Any]:
    hvac = getattr(snap, "hvac", None) if snap is not None else None
    realtime = getattr(snap, "realtime", None) if snap is not None else None
    return build_seat_climate_params(hvac, realtime, steering_on=True)


def _steering_params_off(snap: Any) -> dict[str, Any]:
    hvac = getattr(snap, "hvac", None) if snap is not None else None
    realtime = getattr(snap, "realtime", None) if snap is not None else None
    return build_seat_climate_params(hvac, realtime, steering_on=False)


SWITCH_DESCRIPTIONS: tuple[BydSwitchDescription, ...] = (
    BydSwitchDescription(
        key="find_car",
        icon="mdi:car-search",
        command_on="FINDCAR",
        pulse=True,
    ),
    BydSwitchDescription(
        key="flash_lights",
        icon="mdi:car-light-high",
        command_on="FLASHLIGHTNOWHISTLE",
        pulse=True,
    ),
    BydSwitchDescription(
        key="battery_heat",
        icon="mdi:battery-heart-variant",
        command_on="BATTERYHEAT",
        command_off="BATTERYHEAT",
        params_on=_battery_params_on,
        params_off=_battery_params_off,
        is_on_fn=_snapshot_is_battery_heating,
    ),
    BydSwitchDescription(
        key="steering_wheel_heat",
        icon="mdi:steering",
        command_on="VENTILATIONHEATING",
        command_off="VENTILATIONHEATING",
        params_on=_steering_params_on,
        params_off=_steering_params_off,
        is_on_fn=_snapshot_is_steering_heating,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, BydDataUpdateCoordinator] = data["coordinators"]

    entities: list[SwitchEntity] = []
    for vin, coordinator in coordinators.items():
        vehicle = coordinator.vehicle
        for description in SWITCH_DESCRIPTIONS:
            entities.append(BydSwitch(coordinator, vin, vehicle, description))

    async_add_entities(entities)


class BydSwitch(BydVehicleEntity, SwitchEntity):
    """BYD remote-control switch."""

    _attr_has_entity_name = True
    entity_description: BydSwitchDescription

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        vin: str,
        vehicle: Vehicle,
        description: BydSwitchDescription,
    ) -> None:
        self._vin = vin
        self._vehicle = vehicle
        self.entity_description = description
        self._attr_translation_key = description.key
        self._attr_unique_id = f"{vin}_switch_{description.key}"
        super().__init__(coordinator)
        self._optimistic_on: bool | None = None
        self._optimistic_until: float = 0.0
        self._pulse_unsub: Callable[[], None] | None = None

    def _is_optimistic_valid(self) -> bool:
        return _time.time() < self._optimistic_until

    def _cancel_pulse(self) -> None:
        if self._pulse_unsub is not None:
            self._pulse_unsub()
            self._pulse_unsub = None

    def _set_optimistic(self, is_on: bool, ttl: float = _OPTIMISTIC_TTL) -> None:
        self._optimistic_on = is_on
        self._optimistic_until = _time.time() + ttl
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        if self._is_optimistic_valid() and self._optimistic_on is not None:
            return self._optimistic_on
        fn = self.entity_description.is_on_fn
        if fn is None:
            return False
        value = fn(self._snapshot())
        return bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, turn_on: bool) -> None:
        description = self.entity_description
        if not turn_on and description.pulse:
            self._cancel_pulse()
            self._optimistic_on = False
            self._optimistic_until = 0.0
            self.async_write_ha_state()
            return
        if not turn_on and not description.command_off:
            return

        command = description.command_on if turn_on else description.command_off
        if command is None:
            return
        builder = description.params_on if turn_on else description.params_off
        params = builder(self._snapshot()) if builder is not None else None
        self._cancel_pulse()
        self._set_optimistic(turn_on)
        try:
            await self.coordinator.execute_control(command, params)
        except Exception as exc:
            self._optimistic_on = None
            self._optimistic_until = 0.0
            _LOGGER.error("%s failed: %s", command, exc)
            raise

        if description.pulse:
            self._schedule_pulse_off()

    def _schedule_pulse_off(self) -> None:
        self._cancel_pulse()

        @callback
        def _auto_off(_now: Any) -> None:
            self._pulse_unsub = None
            self._optimistic_on = False
            self._optimistic_until = 0.0
            self.async_write_ha_state()

        self._pulse_unsub = async_call_later(self.hass, _PULSE_SECONDS, _auto_off)

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_pulse()
        await super().async_will_remove_from_hass()
