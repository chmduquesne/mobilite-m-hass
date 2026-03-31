"""Sensor platform for Mobilités-M: next departure timestamps per direction."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_AVAILABLE_DIRECTIONS,
    CONF_CLUSTER_NAME,
    CONF_DIRECTION_FILTER,
    DOMAIN,
)
from . import MobiliteMCoordinator

_NB_SLOTS = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up departure sensors from a config entry."""
    coordinator: MobiliteMCoordinator = hass.data[DOMAIN][entry.entry_id]
    cluster_name: str = entry.data[CONF_CLUSTER_NAME]

    direction_filter: list[str] = entry.data.get(CONF_DIRECTION_FILTER, [])
    # {desc: label} e.g. {"Montfleury": "17 → Montfleury"}
    available_directions: dict[str, str] = entry.data.get(CONF_AVAILABLE_DIRECTIONS, {})

    if direction_filter:
        directions = {d: available_directions.get(d, d) for d in direction_filter}
    else:
        directions = available_directions

    async_add_entities(
        MobiliteMDepartureSensor(coordinator, entry.entry_id, cluster_name, direction, label, i)
        for direction, label in directions.items()
        for i in range(_NB_SLOTS)
    )


class MobiliteMDepartureSensor(CoordinatorEntity, SensorEntity):
    """Sensor representing the n-th next departure for a specific direction."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MobiliteMCoordinator,
        entry_id: str,
        cluster_name: str,
        direction: str,
        label: str,
        index: int,
    ) -> None:
        super().__init__(coordinator)
        self._direction = direction
        self._label = label
        self._index = index
        self._cluster_name = cluster_name
        self._attr_unique_id = f"{entry_id}_{direction}_{index}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.coordinator.name)},
            "name": self._cluster_name,
            "manufacturer": "Mobilités-M",
            "model": "Stop cluster",
        }

    def _departures_for_direction(self) -> list[dict]:
        return [
            dep
            for dep in (self.coordinator.data or [])
            if dep.get("direction") == self._direction
        ]

    @property
    def name(self) -> str:
        if self._index == 0:
            return self._label
        return f"{self._label} {self._index + 1}"

    @property
    def native_value(self) -> datetime | None:
        deps = self._departures_for_direction()
        if self._index >= len(deps):
            return None
        return datetime.fromtimestamp(deps[self._index]["timestamp"], tz=timezone.utc)

    @property
    def extra_state_attributes(self) -> dict:
        deps = self._departures_for_direction()
        if self._index >= len(deps):
            return {}
        dep = deps[self._index]
        return {
            "line": dep.get("line"),
            "direction": dep.get("direction"),
            "delay_minutes": dep.get("delay_minutes", 0),
            "realtime": dep.get("realtime", False),
            "occupancy": dep.get("occupancy"),
            "stop_name": dep.get("stop_name"),
        }
