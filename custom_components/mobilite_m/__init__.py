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
        self._direction_filter: list[str] = entry.data.get(CONF_DIRECTION_FILTER, [])
        available: dict[str, str] = entry.data.get(CONF_AVAILABLE_DIRECTIONS, {})
        # All (line, direction) pairs used for scheduled fallback queries.
        self._tracked: set[tuple[str, str]] = {
            (k.split("|")[0], k.split("|")[1])
            for k in (self._direction_filter if self._direction_filter else available)
        }
        self._stop_ids: list[str] | None = None
        self._stop_coords: dict[str, tuple[float, float]] | None = None
        self._stop_names: dict[str, str] | None = None
        self._route_metadata: dict[str, dict[str, str]] | None = None
        self._route_ids: dict[str, str] = {}  # {line: full_route_id} e.g. {"T65": "C38:T65"}
        self._direction_origin: dict[tuple[str, str], tuple[str, str]] = {}  # (line, direction) → (stop_id, name)

    @property
    def route_modes(self) -> dict[str, str]:
        """Return cached {line: mode} mapping."""
        return {
            line: metadata.get("mode", "")
            for line, metadata in (self._route_metadata or {}).items()
        }

    @property
    def route_metadata(self) -> dict[str, dict[str, str]]:
        """Return cached Home Assistant-ready metadata for each line."""
        return self._route_metadata or {}

    async def _ensure_route_modes(self) -> None:
        """Fetch and cache route metadata for each line at this cluster."""
        if self._route_metadata is not None:
            return
        try:
            async with self._session.get(
                f"{BASE_URL}/api/routers/default/index/clusters/{self._cluster_code}/routes",
                headers={"Origin": ORIGIN_HEADER},
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("Unable to fetch route metadata: API returned %s", resp.status)
                    return
                routes = await resp.json(content_type=None)
        except Exception as err:
            _LOGGER.warning("Unable to fetch route metadata: %s", err)
            return

        self._route_metadata = {}
        self._route_ids = {}
        for r in routes:
            rid = r.get("id", "")
            if rid and ":" in rid:
                line = rid.split(":")[1]
                metadata = {
                    "color": _format_color(r.get("color")),
                    "text_color": _format_color(r.get("textColor")),
                    "short_name": r.get("shortName") or line,
                    "long_name": r.get("longName"),
                    "mode": r.get("mode"),
                    "type": r.get("type"),
                }
                self._route_metadata[line] = {
                    key: value for key, value in metadata.items() if value is not None
                }
                self._route_ids[line] = rid

    @property
    def stop_ids(self) -> list[str]:
        """Return cached stop IDs for the cluster."""
        return self._stop_ids or []

    @property
    def stop_coords(self) -> dict[str, tuple[float, float]]:
        """Return cached {stop_id: (lat, lon)} mapping."""
        return self._stop_coords or {}

    @property
    def stop_names(self) -> dict[str, str]:
        """Return cached {stop_id: name} mapping."""
        return self._stop_names or {}

    @property
    def direction_origin(self) -> dict[tuple[str, str], tuple[str, str]]:
        """Return cached {(line, direction): (origin_stop_id, origin_name)} from ficheHoraires."""
        return self._direction_origin

    async def _ensure_stops(self) -> None:
        """Fetch and cache stop IDs, coordinates, and names from the cluster GeoJSON."""
        if self._stop_ids is not None:
            return
        try:
            async with self._session.get(
                f"{BASE_URL}/api/clusters/{self._cluster_code}/stops",
                headers={"Origin": ORIGIN_HEADER},
            ) as resp:
                data = await resp.json(content_type=None) if resp.status == 200 else {}
        except Exception:
            data = {}
        self._stop_ids = []
        self._stop_coords = {}
        self._stop_names = {}
        for f in data.get("features", []):
            props = f.get("properties", {})
            stop_id = props.get("id") or props.get("gtfsId")
            if not stop_id:
                continue
            self._stop_ids.append(stop_id)
            if name := props.get("name"):
                self._stop_names[stop_id] = name
            coords = f.get("geometry", {}).get("coordinates")
            if coords and len(coords) >= 2:
                # GeoJSON coordinates are [longitude, latitude]
                self._stop_coords[stop_id] = (coords[1], coords[0])

    async def _ensure_direction_origins(self, pairs: set[tuple[str, str]]) -> None:
        """Fetch ficheHoraires once per line to cache origin stop info for given (line, direction) pairs."""
        lines_needed = {line for line, direction in pairs if (line, direction) not in self._direction_origin}
        if not lines_needed:
            return
        today_ms = int(datetime(
            *date.today().timetuple()[:3], tzinfo=timezone.utc
        ).timestamp() * 1000)
        for line in lines_needed:
            try:
                async with self._session.get(
                    f"{BASE_URL}/api/ficheHoraires/json",
                    params={"route": self._route_ids.get(line, f"SEM:{line}"), "time": today_ms, "nbTrips": 1},
                    headers={"Origin": ORIGIN_HEADER},
                ) as resp:
                    fiche = await resp.json(content_type=None) if resp.status == 200 else {}
            except Exception:
                continue
            for fd in fiche.values():
                arrets = fd.get("arrets", [])
                if not arrets:
                    continue
                last_name = arrets[-1].get("name", "")
                if last_name:
                    self._direction_origin[(line, last_name)] = (
                        arrets[0].get("stopId", ""),
                        arrets[0].get("name", ""),
                    )

    async def _async_update_data(self) -> list[dict]:
        """Fetch next departures from the API, falling back to scheduled data if needed."""
        await self._ensure_route_modes()
        await self._ensure_stops()
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

        departures = _parse_departures(data, self._direction_filter)
        pairs = {(dep["line"], dep["direction"]) for dep in departures if dep.get("line") and dep.get("direction")}
        await self._ensure_direction_origins(pairs)
        return await self._fill_scheduled(departures)

    async def _fill_scheduled(self, departures: list[dict]) -> list[dict]:
        """Append scheduled departures until NB_SLOTS total departures are available.

        Uses ficheHoraires which covers both timepoint and non-timepoint stops.
        """
        if len(departures) >= NB_SLOTS:
            return departures

        stop_ids = self._stop_ids or []
        if not stop_ids:
            return departures

        existing_ts = {dep["timestamp"] for dep in departures}
        today = date.today()
        stop_ids_set = set(stop_ids)
        lines = {line for line, _ in self._tracked}

        for days_ahead in range(1, 15):
            if len(departures) >= NB_SLOTS:
                break
            future_date = today + timedelta(days=days_ahead)
            service_day_ms = int(datetime(
                future_date.year, future_date.month, future_date.day,
                tzinfo=timezone.utc,
            ).timestamp() * 1000)
            service_day_unix = service_day_ms // 1000

            for line in list(lines):
                if len(departures) >= NB_SLOTS:
                    break
                dirs_for_line = {d for l, d in self._tracked if l == line}
                try:
                    async with self._session.get(
                        f"{BASE_URL}/api/ficheHoraires/json",
                        params={
                            "route": self._route_ids.get(line, f"SEM:{line}"),
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
                unmatched_dirs = list(dirs_for_line)
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
                    if len(departures) >= NB_SLOTS:
                        break
                    fd = fiche[fk]
                    arrets = fd.get("arrets", [])
                    if arrets and (line, direction) not in self._direction_origin:
                        self._direction_origin[(line, direction)] = (
                            arrets[0].get("stopId", ""),
                            arrets[0].get("name", ""),
                        )
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
                            "stop_id": cluster_arret.get("stopId", ""),
                            "from_calendar": True,
                            "origin_stop_id": arrets[0].get("stopId", ""),
                            "origin_name": arrets[0].get("name", ""),
                            "destination_stop_id": arrets[-1].get("stopId", ""),
                        })
                        existing_ts.add(absolute_ts)

        return sorted(departures, key=lambda d: d["timestamp"])


def _format_color(value: object) -> str | None:
    """Format a route color for direct use in Home Assistant templates."""
    if not isinstance(value, str) or not value.strip():
        return None
    return f"#{value.strip().lstrip('#')}"


def _parse_departures(
    raw: list[dict],
    direction_filter: list[str] | None = None,
) -> list[dict]:
    """Parse and flatten the stoptimes response into a sorted departure list.

    Response format: [{pattern: {id, desc, ...}, times: [{stopId, stopName,
    scheduledDeparture, realtimeDeparture, departureDelay, realtime, serviceDay, ...}]}]
    Line name is the second segment of the pattern id (e.g. "SEM:D:1:..." -> "D").
    """
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
                    "stop_id": time_entry.get("stopId", ""),
                    "origin_stop_id": "",
                    "origin_name": "",
                    "destination_stop_id": "",
                }
            )

    departures.sort(key=lambda d: d["timestamp"])
    return departures
