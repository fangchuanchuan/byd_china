"""Cover entities for BYD windows and trunk.

These use HA cover domain so Bemfa Cloud can import them as curtains/doors.
"""

from __future__ import annotations

import logging
import time as _time
from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .pybyd_china.models.vehicle import Vehicle

from .const import DOMAIN
from .coordinator import BydDataUpdateCoordinator
from .entity import BydVehicleEntity
from .remote import trunk_is_closed, windows_are_closed

_LOGGER = logging.getLogger(__name__)

_OPTIMISTIC_TTL = 30.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, BydDataUpdateCoordinator] = data["coordinators"]

    entities: list[CoverEntity] = []
    for vin, coordinator in coordinators.items():
        vehicle = coordinator.vehicle
        entities.append(BydWindowsCover(coordinator, vin, vehicle))
        entities.append(BydTrunkCover(coordinator, vin, vehicle))

    async_add_entities(entities)


class _BydCover(BydVehicleEntity, CoverEntity):
    """Cover with short-lived optimistic open/closed state."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        vin: str,
        vehicle: Vehicle,
    ) -> None:
        self._vin = vin
        self._vehicle = vehicle
        super().__init__(coordinator)
        self._optimistic_closed: bool | None = None
        self._optimistic_until: float = 0.0

    def _is_optimistic_valid(self) -> bool:
        return _time.time() < self._optimistic_until

    def _set_optimistic(self, closed: bool) -> None:
        self._optimistic_closed = closed
        self._optimistic_until = _time.time() + _OPTIMISTIC_TTL
        self.async_write_ha_state()

    async def _send(self, command_type: str, closed: bool) -> None:
        self._set_optimistic(closed)
        try:
            await self.coordinator.execute_control(command_type)
        except Exception as exc:
            self._optimistic_closed = None
            self._optimistic_until = 0.0
            _LOGGER.error("%s failed: %s", command_type, exc)
            raise


class BydWindowsCover(_BydCover):
    """All side windows as a single cover.

    Open sends OPENWINDOW (BYD vent / ajar, not full-drop).
    Close sends CLOSEWINDOW.
    """

    _attr_translation_key = "windows"
    _attr_device_class = CoverDeviceClass.WINDOW
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_icon = "mdi:car-door"

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        vin: str,
        vehicle: Vehicle,
    ) -> None:
        super().__init__(coordinator, vin, vehicle)
        self._attr_unique_id = f"{vin}_cover_windows"

    @property
    def is_closed(self) -> bool | None:
        if self._is_optimistic_valid() and self._optimistic_closed is not None:
            return self._optimistic_closed
        return windows_are_closed(self._get_realtime())

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._send("OPENWINDOW", closed=False)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._send("CLOSEWINDOW", closed=True)


class BydTrunkCover(_BydCover):
    """Trunk lid as a cover."""

    _attr_translation_key = "trunk"
    _attr_device_class = CoverDeviceClass.DOOR
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_icon = "mdi:car-back"

    def __init__(
        self,
        coordinator: BydDataUpdateCoordinator,
        vin: str,
        vehicle: Vehicle,
    ) -> None:
        super().__init__(coordinator, vin, vehicle)
        self._attr_unique_id = f"{vin}_cover_trunk"

    @property
    def is_closed(self) -> bool | None:
        if self._is_optimistic_valid() and self._optimistic_closed is not None:
            return self._optimistic_closed
        return trunk_is_closed(self._get_realtime())

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._send("OPENTRUNK", closed=False)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._send("CLOSETRUNK", closed=True)
