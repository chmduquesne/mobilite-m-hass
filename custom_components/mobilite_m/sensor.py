"""Sensor platform for Mobilités-M: next departure timestamps."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CLUSTER_NAME, CONF_NB_DEPARTURES, DOMAIN
from . import MobiliteMCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up departure sensors from a config entry."""
    coordinator: MobiliteMCoordinator = hass.data[DOMAIN][entry.entry_id]
    cluster_name: str = entry.data[CONF_CLUSTER_NAME]
    nb: int = entry.data.get(CONF_NB_DEPARTURES, 3)

    async_add_entities(
        MobiliteMDepartureSensor(coordinator, entry.entry_id, cluster_name, i)
        for i in range(nb)
    )


class MobiliteMDepartureSensor(CoordinatorEntity, SensorEntity):
    """Sensor representing the n-th next departure at a stop cluster."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MobiliteMCoordinator,
        entry_id: str,
        cluster_name: str,
        index: int,
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._cluster_name = cluster_name
        self._attr_unique_id = f"{entry_id}_departure_{index}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.coordinator.name)},
            "name": self._cluster_name,
            "manufacturer": "Mobilités-M",
            "model": "Stop cluster",
        }

    @property
    def name(self) -> str:
        """Return sensor name including line and direction from current data."""
        departures: list[dict] = self.coordinator.data or []
        if self._index >= len(departures):
            return f"Départ {self._index + 1}"
        dep = departures[self._index]
        line = dep.get("line", "")
        direction = dep.get("direction", "")
        if line and direction:
            return f"{line} → {direction}"
        return f"Départ {self._index + 1}"

    @property
    def native_value(self) -> datetime | None:
        """Return the departure time as an aware datetime."""
        departures: list[dict] = self.coordinator.data or []
        if self._index >= len(departures):
            return None
        ts = departures[self._index]["timestamp"]
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    @property
    def extra_state_attributes(self) -> dict:
        """Return line, direction, delay, realtime flag, occupancy, stop name."""
        departures: list[dict] = self.coordinator.data or []
        if self._index >= len(departures):
            return {}
        dep = departures[self._index]
        return {
            "line": dep.get("line"),
            "direction": dep.get("direction"),
            "delay_minutes": dep.get("delay_minutes", 0),
            "realtime": dep.get("realtime", False),
            "occupancy": dep.get("occupancy"),
            "stop_name": dep.get("stop_name"),
        }
