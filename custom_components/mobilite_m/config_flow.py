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
    """Await an aiohttp request coroutine and return parsed JSON, or default on error."""
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
        self._routes_data: list[dict] = []
        self._patterns_data: dict = {}
        self._available_modes: dict[str, str] = {}
        self._selected_modes: list[str] = []
        self._available_lines: dict[str, str] = {}
        self._selected_lines: list[str] = []
        self._available_destinations: dict[str, str] = {}

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

            session = async_get_clientsession(self.hass)
            headers = {"Origin": ORIGIN_HEADER}
            self._routes_data, self._patterns_data = await asyncio.gather(
                _get_json(
                    session.get(
                        f"{BASE_URL}/api/routers/default/index/clusters/{code}/routes",
                        headers=headers,
                    ),
                    default=[],
                ),
                _get_json(
                    session.get(
                        f"{BASE_URL}/api/clusters/{code}/patterns",
                        headers=headers,
                    )
                ),
            )
            self._available_modes = self._compute_modes()
            return await self.async_step_select_modes()

        return self.async_show_form(
            step_id="select_cluster",
            data_schema=vol.Schema(
                {vol.Required("cluster"): vol.In(self._clusters)}
            ),
        )

    async def async_step_select_modes(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3: Optionally filter by transport mode."""
        if len(self._available_modes) <= 1:
            user_input = {"modes": []}

        if user_input is not None:
            self._selected_modes = user_input.get("modes", [])
            self._available_lines = self._compute_lines()
            return await self.async_step_select_lines()

        return self.async_show_form(
            step_id="select_modes",
            data_schema=vol.Schema({
                vol.Optional("modes", default=[]): cv.multi_select(self._available_modes)
            }),
        )

    async def async_step_select_lines(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 4: Optionally filter by route/line."""
        if len(self._available_lines) <= 1:
            user_input = {"lines": []}

        if user_input is not None:
            self._selected_lines = user_input.get("lines", [])
            self._available_destinations = self._compute_destinations()
            return await self.async_step_select_destinations()

        return self.async_show_form(
            step_id="select_lines",
            data_schema=vol.Schema({
                vol.Optional("lines", default=[]): cv.multi_select(self._available_lines)
            }),
        )

    async def async_step_select_destinations(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 5: Optionally filter by destination."""
        if len(self._available_destinations) <= 1:
            user_input = {"destinations": []}

        if user_input is not None:
            direction_filter = user_input.get("destinations", [])

            effective_filter = direction_filter or sorted(self._available_destinations.keys())
            await self.async_set_unique_id(
                f"{self._selected_cluster_code}_{'_'.join(effective_filter)}"
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=self._build_title(direction_filter),
                data={
                    CONF_CLUSTER_CODE: self._selected_cluster_code,
                    CONF_CLUSTER_NAME: self._selected_cluster_name,
                    CONF_DIRECTION_FILTER: direction_filter,
                    CONF_AVAILABLE_DIRECTIONS: self._available_destinations,
                },
            )

        return self.async_show_form(
            step_id="select_destinations",
            data_schema=vol.Schema({
                vol.Optional("destinations", default=[]): cv.multi_select(
                    self._available_destinations
                )
            }),
        )

    def _compute_modes(self) -> dict[str, str]:
        seen: dict[str, str] = {}
        for r in self._routes_data:
            mode = r.get("mode", "")
            if mode and mode not in seen:
                seen[mode] = _MODE_LABELS.get(mode, mode)
        return seen

    def _compute_lines(self) -> dict[str, str]:
        mode_set = set(self._selected_modes)
        route_modes: dict[str, str] = {
            r["id"]: r.get("mode", "") for r in self._routes_data if r.get("id")
        }
        seen: dict[str, str] = {}
        for stop_patterns in self._patterns_data.values():
            for pattern in stop_patterns:
                pid = pattern.get("id", "")
                line = pid.split(":")[1] if ":" in pid else ""
                if not line:
                    continue
                route_id = ":".join(pid.split(":")[:2])
                mode = route_modes.get(route_id, "")
                if mode_set and mode not in mode_set:
                    continue
                if line not in seen:
                    label = f"{_MODE_LABELS.get(mode, mode)} · {line}" if mode else line
                    seen[line] = label
        return seen

    def _compute_destinations(self) -> dict[str, str]:
        route_set = set(self._selected_lines)
        pairs: dict[tuple[str, str], None] = {}
        for stop_patterns in self._patterns_data.values():
            for pattern in stop_patterns:
                pid = pattern.get("id", "")
                line = pid.split(":")[1] if ":" in pid else ""
                desc = pattern.get("desc", "")
                if not line or not desc:
                    continue
                if route_set and line not in route_set:
                    continue
                pairs[(line, desc)] = None
        return {f"{line}|{desc}": f"{line} → {desc}" for line, desc in pairs}

    def _build_title(self, direction_filter: list[str]) -> str:
        modes_label = (
            ", ".join(_MODE_LABELS.get(m, m) for m in self._selected_modes)
            or "All modes"
        )
        lines_label = ", ".join(self._selected_lines) or "All lines"
        if not direction_filter:
            dests_label = "All destinations"
        else:
            dests_seen: list[str] = []
            seen_d: set[str] = set()
            for d in direction_filter:
                _, _, dest = d.partition("|")
                if dest not in seen_d:
                    dests_seen.append(dest)
                    seen_d.add(dest)
            dests_label = "→ " + ", ".join(dests_seen)
        return f"{self._selected_cluster_name} | {modes_label} · {lines_label} · {dests_label}"

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
