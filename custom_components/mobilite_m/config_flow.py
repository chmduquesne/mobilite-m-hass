"""Config flow for Mobilités-M integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .const import (
    BASE_URL,
    CONF_AVAILABLE_DIRECTIONS,
    CONF_CLUSTER_CODE,
    CONF_CLUSTER_NAME,
    CONF_DIRECTION_FILTER,
    CONF_STOP_FILTER,
    DOMAIN,
    ORIGIN_HEADER,
)

_LOGGER = logging.getLogger(__name__)

_MODE_LABELS = {
    "BUS": "Bus",
    "TRAM": "Tram",
    "RAIL": "Train",
    "SUBWAY": "Metro",
    "FERRY": "Ferry",
    "FUNICULAR": "Funicular",
    "CABLE_CAR": "Cable car",
}


async def _get_json(coro, default=None):
    """Await a aiohttp request coroutine and return parsed JSON, or default on error."""
    if default is None:
        default = {}
    async with coro as resp:
        return await resp.json(content_type=None) if resp.status == 200 else default


class MobiliteMConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mobilités-M."""

    VERSION = 1

    def __init__(self) -> None:
        self._clusters: dict[str, str] = {}
        self._selected_cluster_code: str = ""
        self._selected_cluster_name: str = ""
        self._available_stops: dict[str, str] = {}
        self._selected_stop_filter: list[str] = []
        self._available_directions: dict[str, str] = {}  # desc -> "line - desc"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1: Search for a stop cluster by name."""
        errors: dict[str, str] = {}

        if user_input is not None:
            query = user_input.get("stop_query", "").strip()
            if len(query) < 3:
                errors["stop_query"] = "query_too_short"
            else:
                clusters = await self._search_clusters(query)
                if not clusters:
                    errors["stop_query"] = "no_results"
                else:
                    self._clusters = clusters
                    return await self.async_step_select_cluster()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("stop_query"): str}),
            errors=errors,
        )

    async def async_step_select_cluster(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2: Choose the cluster from search results."""
        if user_input is not None:
            code = user_input["cluster"]
            self._selected_cluster_code = code
            self._selected_cluster_name = self._clusters[code]
            self._available_stops = await self._fetch_stops(code)
            return await self.async_step_select_stops()

        return self.async_show_form(
            step_id="select_cluster",
            data_schema=vol.Schema(
                {vol.Required("cluster"): vol.In(self._clusters)}
            ),
        )

    async def async_step_select_stops(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3: Optionally filter by stop poles (skipped if only one)."""
        if len(self._available_stops) <= 1:
            user_input = {"stop_filter": []}

        if user_input is not None:
            self._selected_stop_filter = user_input.get("stop_filter", [])
            self._available_directions = await self._fetch_directions(
                self._selected_cluster_code, self._selected_stop_filter
            )
            return await self.async_step_select_directions()

        schema_fields: dict = {}
        if self._available_stops:
            schema_fields[vol.Optional("stop_filter", default=[])] = cv.multi_select(
                self._available_stops
            )

        return self.async_show_form(
            step_id="select_stops",
            data_schema=vol.Schema(schema_fields),
        )

    async def async_step_select_directions(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 4: Optionally filter by direction (skipped if only one)."""
        if len(self._available_directions) <= 1:
            user_input = {"direction_filter": []}

        if user_input is not None:
            direction_filter = user_input.get("direction_filter", [])

            await self.async_set_unique_id(
                f"{self._selected_cluster_code}"
                f"_{'_'.join(sorted(self._selected_stop_filter))}"
                f"_{'_'.join(sorted(direction_filter))}"
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=self._selected_cluster_name,
                data={
                    CONF_CLUSTER_CODE: self._selected_cluster_code,
                    CONF_CLUSTER_NAME: self._selected_cluster_name,
                    CONF_STOP_FILTER: self._selected_stop_filter,
                    CONF_DIRECTION_FILTER: direction_filter,
                    CONF_AVAILABLE_DIRECTIONS: self._available_directions,
                },
            )

        schema_fields: dict = {}
        if self._available_directions:
            schema_fields[vol.Optional("direction_filter", default=[])] = cv.multi_select(
                self._available_directions
            )

        return self.async_show_form(
            step_id="select_directions",
            data_schema=vol.Schema(schema_fields),
        )

    async def _search_clusters(self, query: str) -> dict[str, str]:
        """Search stop clusters by name, returning {code: label}."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"{BASE_URL}/api/points/json",
                params={"types": "clusters", "query": query},
                headers={"Origin": ORIGIN_HEADER},
            ) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json(content_type=None)
        except Exception:
            _LOGGER.exception("Error searching clusters")
            return {}

        results: dict[str, str] = {}
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            if not props.get("visible", True):
                continue
            code = props.get("code") or props.get("id")
            name = props.get("name")
            city = props.get("city", "")
            if code and name:
                results[code] = f"{name} ({city})" if city else name

        # Append cluster code to labels that are not unique after visibility filtering
        seen: dict[str, list[str]] = {}
        for code, label in results.items():
            seen.setdefault(label, []).append(code)
        for label, codes in seen.items():
            if len(codes) > 1:
                for code in codes:
                    results[code] = f"{label} [{code}]"

        return results

    async def _fetch_stops(self, cluster_code: str) -> dict[str, str]:
        """Fetch stop poles within a cluster, returning {stop_id: label}.

        Labels show the transport type and destinations, e.g.
        "Bus stop: 17 → Montfleury, N62 → Col de Porte".
        """
        session = async_get_clientsession(self.hass)
        headers = {"Origin": ORIGIN_HEADER}
        try:
            stops_data, patterns_data, routes_data = await asyncio.gather(
                _get_json(session.get(f"{BASE_URL}/api/clusters/{cluster_code}/stops", headers=headers)),
                _get_json(session.get(f"{BASE_URL}/api/clusters/{cluster_code}/patterns", headers=headers)),
                _get_json(session.get(f"{BASE_URL}/api/routers/default/index/clusters/{cluster_code}/routes", headers=headers), default=[]),
            )
        except Exception:
            _LOGGER.exception("Error fetching stops for cluster %s", cluster_code)
            return {}

        # Build {route_id: mode} map, e.g. {"SEM:17": "BUS", "SEM:A": "TRAM"}
        route_modes: dict[str, str] = {
            r["id"]: r.get("mode", "")
            for r in routes_data
            if r.get("id")
        }

        # Build {stop_id: (dominant_mode, ["line → dest", ...])} from patterns
        stop_info: dict[str, tuple[str, list[str]]] = {}
        for stop_id, patterns in patterns_data.items():
            seen: set[str] = set()
            dests: list[str] = []
            mode_counts: dict[str, int] = {}
            for pattern in patterns:
                pattern_id = pattern.get("id", "")
                route_id = ":".join(pattern_id.split(":")[:2])
                line = pattern_id.split(":")[1] if ":" in pattern_id else ""
                desc = pattern.get("desc", "")
                label = f"{line} → {desc}" if line and desc else desc
                if label and label not in seen:
                    seen.add(label)
                    dests.append(label)
                mode = route_modes.get(route_id, "")
                if mode:
                    mode_counts[mode] = mode_counts.get(mode, 0) + 1
            dominant_mode = max(mode_counts, key=mode_counts.get) if mode_counts else ""
            stop_info[stop_id] = (dominant_mode, dests)

        result: dict[str, str] = {}
        for feature in stops_data.get("features", []):
            props = feature.get("properties", {})
            stop_id = props.get("id") or props.get("gtfsId")
            name = props.get("name", stop_id)
            if not stop_id:
                continue
            mode, dests = stop_info.get(stop_id, ("", []))
            prefix = _MODE_LABELS.get(mode, "Stop")
            if dests:
                result[stop_id] = f"{prefix}: {', '.join(dests)}"
            else:
                result[stop_id] = f"{prefix}: {name}"
        return result

    async def _fetch_directions(
        self,
        cluster_code: str,
        stop_filter: list[str],
    ) -> dict[str, str]:
        """Fetch available directions, returning {"line|desc": "line → desc"}.

        Each unique (line, destination) pair becomes one entry.
        The key is used for filtering in the coordinator; the value is the UI label.
        """
        session = async_get_clientsession(self.hass)
        try:
            data = await _get_json(
                session.get(
                    f"{BASE_URL}/api/clusters/{cluster_code}/patterns",
                    headers={"Origin": ORIGIN_HEADER},
                )
            )
        except Exception:
            _LOGGER.exception("Error fetching patterns for cluster %s", cluster_code)
            return {}

        stop_set = set(stop_filter) if stop_filter else None
        pairs: dict[tuple[str, str], None] = {}
        for stop_id, patterns in data.items():
            if stop_set and stop_id not in stop_set:
                continue
            for pattern in patterns:
                pattern_id = pattern.get("id", "")
                line = pattern_id.split(":")[1] if ":" in pattern_id else ""
                desc = pattern.get("desc", "")
                if line and desc:
                    pairs[(line, desc)] = None

        return {
            f"{line}|{desc}": f"{line} → {desc}"
            for (line, desc) in pairs
        }
