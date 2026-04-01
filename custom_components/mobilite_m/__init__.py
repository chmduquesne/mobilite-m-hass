"""Mobilités-M integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BASE_URL,
    CONF_AVAILABLE_DIRECTIONS,
    CONF_CLUSTER_CODE,
    CONF_DIRECTION_FILTER,
    CONF_STOP_FILTER,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NB_SLOTS,
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
        available: dict[str, str] = entry.data.get(CONF_AVAILABLE_DIRECTIONS, {})
        # All (line, direction) pairs that have sensors — used to detect gaps in real-time data.
        self._tracked: set[tuple[str, str]] = {
            (k.split("|")[0], k.split("|")[1])
            for k in (self._direction_filter if self._direction_filter else available)
        }
        self._stop_ids: list[str] | None = None

    async def _ensure_stop_ids(self) -> list[str]:
        """Return stop IDs for the cluster, fetching from the API once if needed."""
        if self._stop_filter:
            return self._stop_filter
        if self._stop_ids is None:
            try:
                async with self._session.get(
                    f"{BASE_URL}/api/clusters/{self._cluster_code}/stops",
                    headers={"Origin": ORIGIN_HEADER},
                ) as resp:
                    data = await resp.json(content_type=None) if resp.status == 200 else {}
            except Exception:
                return []
            self._stop_ids = [
                props.get("id") or props.get("gtfsId")
                for f in data.get("features", [])
                if (props := f.get("properties", {}))
                and (props.get("id") or props.get("gtfsId"))
            ]
        return self._stop_ids

    async def _async_update_data(self) -> list[dict]:
        """Fetch next departures from the API, falling back to scheduled data if needed."""
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

        departures = _parse_departures(data, self._stop_filter, self._direction_filter)
        return await self._fill_scheduled(departures)

    async def _fill_scheduled(self, departures: list[dict]) -> list[dict]:
        """Append scheduled departures for any tracked (line, direction) with fewer than NB_SLOTS entries.

        Uses ficheHoraires which covers both timepoint and non-timepoint stops.
        Iterates through successive days (up to 14) until every tracked direction
        has NB_SLOTS departures or we run out of days to check.
        """
        def _counts(deps: list[dict]) -> dict[tuple[str, str], int]:
            c: dict[tuple[str, str], int] = {}
            for dep in deps:
                key = (dep["line"], dep["direction"])
                c[key] = c.get(key, 0) + 1
            return c

        needs_more = {pair for pair in self._tracked if _counts(departures).get(pair, 0) < NB_SLOTS}
        if not needs_more:
            return departures

        stop_ids = await self._ensure_stop_ids()
        if not stop_ids:
            return departures

        existing_ts = {dep["timestamp"] for dep in departures}
        today = date.today()
        stop_ids_set = set(stop_ids)
        lines_needing = {line for line, _ in needs_more}

        for days_ahead in range(1, 15):
            if not needs_more:
                break
            future_date = today + timedelta(days=days_ahead)
            service_day_ms = int(datetime(
                future_date.year, future_date.month, future_date.day,
                tzinfo=timezone.utc,
            ).timestamp() * 1000)
            service_day_unix = service_day_ms // 1000

            for line in list(lines_needing):
                dirs_needing = {d for l, d in needs_more if l == line}
                if not dirs_needing:
                    lines_needing.discard(line)
                    continue
                try:
                    async with self._session.get(
                        f"{BASE_URL}/api/ficheHoraires/json",
                        params={
                            "route": f"SEM:{line}",
                            "time": service_day_ms,
                            "nbTrips": NB_SLOTS + 2,
                        },
                        headers={"Origin": ORIGIN_HEADER},
                    ) as resp:
                        fiche = await resp.json(content_type=None) if resp.status == 200 else {}
                except Exception:
                    fiche = {}
                if not fiche:
                    continue

                # Map ficheHoraires directions to tracked directions.
                # First, match by exact last-stop name; then assign remainder by order.
                unmatched_dirs = list(dirs_needing)
                dir_mapping: dict[str, str] = {}
                for fk, fd in fiche.items():
                    arrets = fd.get("arrets", [])
                    if not arrets:
                        continue
                    last_name = arrets[-1].get("name", "")
                    if last_name in unmatched_dirs:
                        dir_mapping[fk] = last_name
                        unmatched_dirs.remove(last_name)
                remaining_fiche = [k for k in fiche if k not in dir_mapping]
                for fk, td in zip(remaining_fiche, unmatched_dirs):
                    dir_mapping[fk] = td

                for fk, direction in dir_mapping.items():
                    if _counts(departures).get((line, direction), 0) >= NB_SLOTS:
                        continue
                    fd = fiche[fk]
                    arrets = fd.get("arrets", [])
                    n_trips = len(fd.get("trips", []))
                    cluster_arret = next(
                        (a for a in arrets if a.get("stopId") in stop_ids_set), None
                    )
                    if cluster_arret is None:
                        continue
                    for trip_time in cluster_arret.get("trips", [])[:n_trips]:
                        if trip_time is None:
                            continue
                        try:
                            absolute_ts = service_day_unix + int(trip_time)
                        except (ValueError, TypeError):
                            continue
                        if absolute_ts in existing_ts:
                            continue
                        departures.append({
                            "timestamp": absolute_ts,
                            "line": line,
                            "direction": direction,
                            "delay_minutes": 0,
                            "realtime": False,
                            "occupancy": None,
                            "stop_name": cluster_arret.get("name", ""),
                            "from_calendar": True,
                        })
                        existing_ts.add(absolute_ts)

            needs_more = {pair for pair in needs_more if _counts(departures).get(pair, 0) < NB_SLOTS}

        return sorted(departures, key=lambda d: d["timestamp"])


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
        line = pattern_id.split(":")[1] if ":" in pattern_id else pattern_id
        direction = pattern.get("desc", "")
        if direction_set and f"{line}|{direction}" not in direction_set:
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
