"""Mobilités-M integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BASE_URL,
    CONF_CLUSTER_CODE,
    CONF_NB_DEPARTURES,
    CONF_ROUTE_FILTER,
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
        self._route_filter: list[str] = entry.data.get(CONF_ROUTE_FILTER, [])
        self.nb_departures: int = entry.data.get(CONF_NB_DEPARTURES, 3)

    async def _async_update_data(self) -> list[dict]:
        """Fetch next departures from the API."""
        url = f"{BASE_URL}/api/routers/default/index/clusters/{self._cluster_code}/stoptimes"
        params = {}
        if self._route_filter:
            params["route"] = ",".join(self._route_filter)

        try:
            async with self._session.get(
                url,
                params=params,
                headers={"Origin": ORIGIN_HEADER},
            ) as resp:
                if resp.status == 204:
                    return []
                if resp.status != 200:
                    raise UpdateFailed(f"API returned status {resp.status}")
                data = await resp.json(content_type=None)
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error fetching departures: {err}") from err

        return _parse_departures(data)


def _parse_departures(raw: list[dict]) -> list[dict]:
    """Parse and flatten the stoptimes response into a sorted departure list."""
    departures = []
    for stop_entry in raw:
        stop_name = stop_entry.get("stop", {}).get("name", "")
        for pattern_entry in stop_entry.get("times", []):
            route_short = pattern_entry.get("pattern", {}).get("route", {}).get("shortName", "")
            headsign = pattern_entry.get("pattern", {}).get("desc", "")
            for time_entry in pattern_entry.get("times", []):
                realtime = time_entry.get("realtime", False)
                scheduled = time_entry.get("scheduledDeparture", 0)
                realtime_departure = time_entry.get("realtimeDeparture", scheduled)
                service_day = time_entry.get("serviceDay", 0)
                occupancy = time_entry.get("occupancyStatus", None)

                # serviceDay is midnight UTC of the service day (unix seconds)
                # scheduledDeparture / realtimeDeparture are seconds since midnight
                departure_ts = service_day + realtime_departure
                delay_seconds = realtime_departure - scheduled

                departures.append(
                    {
                        "timestamp": departure_ts,
                        "line": route_short,
                        "direction": headsign,
                        "delay_minutes": round(delay_seconds / 60),
                        "realtime": realtime,
                        "occupancy": occupancy,
                        "stop_name": stop_name,
                    }
                )

    departures.sort(key=lambda d: d["timestamp"])
    return departures
