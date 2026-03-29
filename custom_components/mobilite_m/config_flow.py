"""Config flow for Mobilités-M integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .const import (
    BASE_URL,
    CONF_CLUSTER_CODE,
    CONF_CLUSTER_NAME,
    CONF_NB_DEPARTURES,
    CONF_ROUTE_FILTER,
    DEFAULT_NB_DEPARTURES,
    DOMAIN,
    ORIGIN_HEADER,
)

_LOGGER = logging.getLogger(__name__)


class MobiliteMConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mobilités-M."""

    VERSION = 1

    def __init__(self) -> None:
        self._clusters: dict[str, str] = {}  # code -> name
        self._selected_cluster_code: str = ""
        self._selected_cluster_name: str = ""
        self._available_routes: dict[str, str] = {}  # code -> label

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
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["cluster"]
            self._selected_cluster_code = code
            self._selected_cluster_name = self._clusters[code]
            self._available_routes = await self._fetch_routes(code)
            return await self.async_step_select_routes()

        cluster_options = {code: name for code, name in self._clusters.items()}
        return self.async_show_form(
            step_id="select_cluster",
            data_schema=vol.Schema(
                {vol.Required("cluster"): vol.In(cluster_options)}
            ),
            errors=errors,
        )

    async def async_step_select_routes(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3: Optionally filter by routes and set preferences."""
        errors: dict[str, str] = {}

        if user_input is not None:
            route_filter = user_input.get("route_filter", [])
            nb_departures = user_input.get("nb_departures", DEFAULT_NB_DEPARTURES)

            await self.async_set_unique_id(
                f"{self._selected_cluster_code}_{','.join(sorted(route_filter))}"
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=self._selected_cluster_name,
                data={
                    CONF_CLUSTER_CODE: self._selected_cluster_code,
                    CONF_CLUSTER_NAME: self._selected_cluster_name,
                    CONF_ROUTE_FILTER: route_filter,
                    CONF_NB_DEPARTURES: nb_departures,
                },
            )

        route_options = {
            code: label for code, label in self._available_routes.items()
        }
        schema_fields: dict = {
            vol.Optional(
                "nb_departures", default=DEFAULT_NB_DEPARTURES
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
        }
        if route_options:
            schema_fields[vol.Optional("route_filter", default=[])] = cv.multi_select(
                route_options
            )

        return self.async_show_form(
            step_id="select_routes",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    async def _search_clusters(self, query: str) -> dict[str, str]:
        """Search stop clusters by name, returning {code: name}."""
        session = async_get_clientsession(self.hass)
        url = f"{BASE_URL}/api/points/json"
        params = {"types": "clusters", "query": query}
        try:
            async with session.get(
                url, params=params, headers={"Origin": ORIGIN_HEADER}
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
            code = props.get("id") or props.get("gtfsId")
            name = props.get("name")
            city = props.get("city", "")
            if code and name:
                label = f"{name} ({city})" if city else name
                results[code] = label
        return results

    async def _fetch_routes(self, cluster_code: str) -> dict[str, str]:
        """Fetch transit routes passing through a cluster, returning {code: label}."""
        session = async_get_clientsession(self.hass)
        url = f"{BASE_URL}/api/routers/default/index/clusters/{cluster_code}/routes"
        try:
            async with session.get(
                url, headers={"Origin": ORIGIN_HEADER}
            ) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json(content_type=None)
        except Exception:
            _LOGGER.exception("Error fetching routes for cluster %s", cluster_code)
            return {}

        routes: dict[str, str] = {}
        for route in data:
            code = route.get("id")
            short = route.get("shortName", code)
            long = route.get("longName", "")
            if code:
                label = f"{short} - {long}" if long else short
                routes[code] = label
        return routes
