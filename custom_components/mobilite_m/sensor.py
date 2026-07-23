"""Sensor platform for Mobilités-M: next departures across all tracked directions."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NB_SLOTS
from . import MobiliteMCoordinator

_MODE_ICONS: dict[str, str] = {
    "BUS": "mdi:bus",
    "TRAM": "mdi:tram",
    "RAIL": "mdi:train",
    "SUBWAY": "mdi:subway",
    "FERRY": "mdi:ferry",
    "FUNICULAR": "mdi:ski-lift",
    "CABLE_CAR": "mdi:gondola",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up departure sensors from a config entry."""
    coordinator: MobiliteMCoordinator = hass.data[DOMAIN][entry.entry_id]
    cluster_name: str = entry.title
    entities: list = [
        MobiliteMDepartureSensor(coordinator, entry.entry_id, cluster_name, i)
        for i in range(NB_SLOTS)
    ]
    entities.append(MobiliteMStopSensor(coordinator, entry.entry_id, cluster_name))
    entities.append(MobiliteMLineSensor(coordinator, entry.entry_id, cluster_name))
    entities.append(MobiliteMOriginSensor(coordinator, entry.entry_id, cluster_name))
    entities.append(MobiliteMDestinationSensor(coordinator, entry.entry_id, cluster_name))
    entities.append(MobiliteMSourceSensor(coordinator, entry.entry_id, cluster_name))
    async_add_entities(entities)


class MobiliteMDepartureSensor(CoordinatorEntity, SensorEntity):
    """Sensor representing the n-th next departure across all tracked directions."""

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
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{index}"

    def _dep(self) -> dict | None:
        deps = self.coordinator.data or []
        return deps[self._index] if self._index < len(deps) else None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "name": self._cluster_name,
            "manufacturer": "Mobilités-M",
            "model": "Stop cluster",
        }

    @property
    def icon(self) -> str:
        dep = self._dep()
        if dep:
            mode = self.coordinator.route_modes.get(dep.get("line", ""), "")
            return _MODE_ICONS.get(mode, "mdi:bus")
        return "mdi:bus"

    @property
    def name(self) -> str:
        if self._index == 0:
            return "Departure"
        dep = self._dep()
        label = f"Departure +{self._index}"
        if dep:
            line = dep.get("line", "")
            direction = dep.get("direction", "")
            if dep.get("from_calendar"):
                badge = " 📅"
            elif dep.get("realtime"):
                badge = " 🔴"
            else:
                badge = ""
            return f"{label} · {line} → {direction}{badge}"
        return label

    @property
    def native_value(self) -> datetime | None:
        dep = self._dep()
        if dep is None:
            return None
        return datetime.fromtimestamp(dep["timestamp"], tz=timezone.utc)

    @property
    def extra_state_attributes(self) -> dict:
        dep = self._dep()
        if dep is None:
            return {}
        attributes = {
            "line": dep.get("line"),
            "direction": dep.get("direction"),
            "delay_minutes": dep.get("delay_minutes", 0),
            "realtime": dep.get("realtime", False),
            "occupancy": dep.get("occupancy"),
            "stop_name": dep.get("stop_name"),
        }
        stop_id = dep.get("stop_id", "")
        if stop_short_name := self.coordinator.stop_names.get(stop_id):
            attributes["stop_short_name"] = stop_short_name
        attributes.update(self.coordinator.route_metadata.get(dep.get("line", ""), {}))
        return attributes


class MobiliteMStopSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing the stop pole coordinates of the next departure."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:bus-stop-covered"

    def __init__(
        self,
        coordinator: MobiliteMCoordinator,
        entry_id: str,
        cluster_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._cluster_name = cluster_name
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_stop"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "name": self._cluster_name,
            "manufacturer": "Mobilités-M",
            "model": "Stop cluster",
        }

    @property
    def name(self) -> str:
        return "Stop"

    @property
    def native_value(self) -> str | None:
        deps = self.coordinator.data or []
        if not deps:
            return None
        stop_id = deps[0].get("stop_id") or ""
        if not stop_id:
            return None
        name = self.coordinator.stop_names.get(stop_id, "")
        return f"{name} ({stop_id})" if name else stop_id

    @property
    def extra_state_attributes(self) -> dict:
        deps = self.coordinator.data or []
        if not deps:
            return {}
        stop_id = deps[0].get("stop_id", "")
        coords = self.coordinator.stop_coords.get(stop_id)
        if coords is None:
            return {}
        return {
            "latitude": coords[0],
            "longitude": coords[1],
        }


class MobiliteMLineSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing the line of the next departure."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MobiliteMCoordinator,
        entry_id: str,
        cluster_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._cluster_name = cluster_name
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_line"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "name": self._cluster_name,
            "manufacturer": "Mobilités-M",
            "model": "Stop cluster",
        }

    _attr_icon = "mdi:ray-start-vertex-end"

    @property
    def name(self) -> str:
        return "Line"

    @property
    def native_value(self) -> str | None:
        deps = self.coordinator.data or []
        if not deps:
            return None
        return deps[0].get("line") or None

    @property
    def extra_state_attributes(self) -> dict:
        """Return metadata for the line of the next departure."""
        deps = self.coordinator.data or []
        if not deps:
            return {}
        return dict(self.coordinator.route_metadata.get(deps[0].get("line", ""), {}))



class MobiliteMOriginSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing the origin stop of the next departure's route."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:ray-start"

    def __init__(
        self,
        coordinator: MobiliteMCoordinator,
        entry_id: str,
        cluster_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._cluster_name = cluster_name
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_origin"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "name": self._cluster_name,
            "manufacturer": "Mobilités-M",
            "model": "Stop cluster",
        }

    @property
    def name(self) -> str:
        return "Origin"

    def _origin(self, dep: dict) -> tuple[str, str]:
        """Return (stop_id, name) for the origin of this departure."""
        stop_id = dep.get("origin_stop_id", "")
        name = dep.get("origin_name", "")
        if not name:
            key = (dep.get("line", ""), dep.get("direction", ""))
            stop_id, name = self.coordinator.direction_origin.get(key, ("", ""))
        return stop_id, name

    @property
    def native_value(self) -> str | None:
        deps = self.coordinator.data or []
        if not deps:
            return None
        _, name = self._origin(deps[0])
        return name or None

    @property
    def extra_state_attributes(self) -> dict:
        deps = self.coordinator.data or []
        if not deps:
            return {}
        origin_id, _ = self._origin(deps[0])
        if not origin_id:
            return {}
        coords = self.coordinator.stop_coords.get(origin_id)
        if coords is None:
            return {}
        return {
            "latitude": coords[0],
            "longitude": coords[1],
        }


class MobiliteMDestinationSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing the destination of the next departure."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:ray-end"

    def __init__(
        self,
        coordinator: MobiliteMCoordinator,
        entry_id: str,
        cluster_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._cluster_name = cluster_name
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_destination"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "name": self._cluster_name,
            "manufacturer": "Mobilités-M",
            "model": "Stop cluster",
        }

    @property
    def name(self) -> str:
        return "Destination"

    @property
    def native_value(self) -> str | None:
        deps = self.coordinator.data or []
        if not deps:
            return None
        return deps[0].get("direction") or None

    @property
    def extra_state_attributes(self) -> dict:
        deps = self.coordinator.data or []
        if not deps:
            return {}
        dest_id = deps[0].get("destination_stop_id", "")
        if not dest_id:
            return {}
        coords = self.coordinator.stop_coords.get(dest_id)
        if coords is None:
            return {}
        return {
            "latitude": coords[0],
            "longitude": coords[1],
        }


class MobiliteMSourceSensor(CoordinatorEntity, SensorEntity):
    """Sensor indicating whether the next departure comes from live tracking or the schedule."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:information-outline"

    def __init__(
        self,
        coordinator: MobiliteMCoordinator,
        entry_id: str,
        cluster_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._cluster_name = cluster_name
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_source"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "name": self._cluster_name,
            "manufacturer": "Mobilités-M",
            "model": "Stop cluster",
        }

    @property
    def name(self) -> str:
        return "Source"

    @property
    def native_value(self) -> str | None:
        deps = self.coordinator.data or []
        if not deps:
            return None
        dep = deps[0]
        if dep.get("from_calendar"):
            return "📅 schedule"
        return "🔴 live"
