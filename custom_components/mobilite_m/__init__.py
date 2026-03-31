"""Mobilités-M integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import timedelta

from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BASE_URL,
    CONF_CLUSTER_CODE,
    CONF_DIRECTION_FILTER,
    CONF_STOP_FILTER,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ORIGIN_HEADER,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Mobilités-M from a config entry."""
    coordinator = MobiliteMCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class MobiliteMCoordinator(DataUpdateCoordinator):
    """Coordinator fetching real-time departures from Mobilités-M API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data[CONF_CLUSTER_CODE]}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self._session = async_get_clientsession(hass)
        self._cluster_code = entry.data[CONF_CLUSTER_CODE]
        self._stop_filter: list[str] = entry.data.get(CONF_STOP_FILTER, [])
        self._direction_filter: list[str] = entry.data.get(CONF_DIRECTION_FILTER, [])

    async def _async_update_data(self) -> list[dict]:
        """Fetch next departures from the API."""
        url = f"{BASE_URL}/api/routers/default/index/clusters/{self._cluster_code}/stoptimes"

        try:
            _LOGGER.debug("Fetching departures from %s", url)
            async with self._session.get(
                URL(url, encoded=True),
                headers={"Origin": ORIGIN_HEADER},
            ) as resp:
                if resp.status == 204:
                    return []
                if resp.status != 200:
                    body = await resp.text()
                    raise UpdateFailed(f"API returned status {resp.status}: {body}")
                data = await resp.json(content_type=None)
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error fetching departures: {err}") from err

        return _parse_departures(data, self._stop_filter, self._direction_filter)


def _parse_departures(
    raw: list[dict],
    stop_filter: list[str] | None = None,
    direction_filter: list[str] | None = None,
) -> list[dict]:
    """Parse and flatten the stoptimes response into a sorted departure list.

    Response format: [{pattern: {id, desc, ...}, times: [{stopId, stopName,
    scheduledDeparture, realtimeDeparture, departureDelay, realtime, serviceDay, ...}]}]
    Line name is the second segment of the pattern id (e.g. "SEM:D:1:..." -> "D").
    """
    stop_set = set(stop_filter) if stop_filter else None
    direction_set = set(direction_filter) if direction_filter else None
    departures = []
    for entry in raw:
        pattern = entry.get("pattern", {})
        pattern_id = pattern.get("id", "")
        parts = pattern_id.split(":")
        line = parts[1] if len(parts) > 1 else pattern_id
        direction = pattern.get("desc", "")
        if direction_set and direction not in direction_set:
            continue

        for time_entry in entry.get("times", []):
            if stop_set and time_entry.get("stopId") not in stop_set:
                continue
            service_day = time_entry.get("serviceDay", 0)
            scheduled = time_entry.get("scheduledDeparture", 0)
            realtime_departure = time_entry.get("realtimeDeparture", scheduled)
            departure_ts = service_day + realtime_departure
            delay_seconds = time_entry.get("departureDelay", 0)

            departures.append(
                {
                    "timestamp": departure_ts,
                    "line": line,
                    "direction": direction,
                    "delay_minutes": round(delay_seconds / 60),
                    "realtime": time_entry.get("realtime", False),
                    "occupancy": time_entry.get("occupancyStatus"),
                    "stop_name": time_entry.get("stopName", ""),
                }
            )

    departures.sort(key=lambda d: d["timestamp"])
    return departures
