#!/usr/bin/env python3
"""Interactive test script for the Mobilités-M API.

Usage:
    pip install aiohttp
    python test_interactive.py
"""
import asyncio
import sys
from datetime import datetime, timezone

import aiohttp

BASE_URL = "https://data.mobilites-m.fr"
HEADERS = {"Origin": "homeassistant-mobilite-m"}


# Reused from custom_components/mobilite_m/__init__.py
def _parse_departures(
    raw: list[dict],
    route_filter: list[str] | None = None,
    direction_filter: list[str] | None = None,
) -> list[dict]:
    route_set = set(route_filter) if route_filter else None
    direction_set = set(direction_filter) if direction_filter else None
    departures = []
    for entry in raw:
        pattern = entry.get("pattern", {})
        pattern_id = pattern.get("id", "")
        route_id = ":".join(pattern_id.split(":")[:2])
        if route_set and route_id not in route_set:
            continue
        parts = pattern_id.split(":")
        line = parts[1] if len(parts) > 1 else pattern_id
        direction = pattern.get("desc", "")
        if direction_set and direction not in direction_set:
            continue
        for time_entry in entry.get("times", []):
            service_day = time_entry.get("serviceDay", 0)
            scheduled = time_entry.get("scheduledDeparture", 0)
            realtime_departure = time_entry.get("realtimeDeparture", scheduled)
            departure_ts = service_day + realtime_departure
            delay_seconds = time_entry.get("departureDelay", 0)
            departures.append({
                "timestamp": departure_ts,
                "line": line,
                "direction": direction,
                "delay_minutes": round(delay_seconds / 60),
                "realtime": time_entry.get("realtime", False),
                "occupancy": time_entry.get("occupancyStatus"),
                "stop_name": time_entry.get("stopName", ""),
            })
    departures.sort(key=lambda d: d["timestamp"])
    return departures


def pick(prompt: str, options: dict) -> tuple[str, str]:
    """Display a numbered menu and return (key, value) of the chosen item."""
    items = list(options.items())
    for i, (_, label) in enumerate(items, 1):
        print(f"  {i}. {label}")
    while True:
        raw = input(f"{prompt} [1-{len(items)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        print("  Invalid choice, try again.")


async def search_clusters(session: aiohttp.ClientSession, query: str) -> dict[str, str]:
    url = f"{BASE_URL}/api/points/json"
    async with session.get(url, params={"types": "clusters", "query": query}, headers=HEADERS) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    results = {}
    for feature in data.get("features", []):
        p = feature.get("properties", {})
        code = p.get("code") or p.get("id")
        name = p.get("name")
        city = p.get("city", "")
        if code and name:
            results[code] = f"{name} ({city})" if city else name
    return results


async def fetch_routes(session: aiohttp.ClientSession, cluster_code: str) -> dict[str, str]:
    url = f"{BASE_URL}/api/routers/default/index/clusters/{cluster_code}/routes"
    async with session.get(url, headers=HEADERS) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    routes = {}
    for route in data:
        code = route.get("id")
        short = route.get("shortName", code)
        long = route.get("longName", "")
        if code:
            routes[code] = f"{short} - {long}" if long else short
    return routes


async def fetch_departures(
    session: aiohttp.ClientSession,
    cluster_code: str,
    route_filter: list[str],
    direction_filter: list[str] | None = None,
) -> list[dict]:
    url = f"{BASE_URL}/api/routers/default/index/clusters/{cluster_code}/stoptimes"
    async with session.get(url, headers=HEADERS) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    return _parse_departures(data, route_filter, direction_filter)


def fmt_time(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%H:%M:%S")


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        # Step 1: search
        while True:
            query = input("\nSearch stop name (min 3 chars): ").strip()
            if len(query) < 3:
                print("  Please enter at least 3 characters.")
                continue
            print(f"  Searching for '{query}'...")
            clusters = await search_clusters(session, query)
            if clusters:
                break
            print("  No results found, try again.")

        # Step 2: pick cluster
        print(f"\nFound {len(clusters)} cluster(s):")
        cluster_code, cluster_name = pick("Select cluster", clusters)
        print(f"  → {cluster_name} ({cluster_code})")

        # Step 3: pick route filter
        print("\nFetching routes at this cluster...")
        routes = await fetch_routes(session, cluster_code)
        route_filter: list[str] = []
        if routes:
            print(f"Found {len(routes)} route(s). Filter by route? (leave blank = all)")
            print("  0. All routes")
            for i, (code, label) in enumerate(routes.items(), 1):
                print(f"  {i}. {label}  [{code}]")
            raw = input("Enter route numbers separated by spaces (or 0/Enter for all): ").strip()
            if raw and raw != "0":
                items = list(routes.items())
                for part in raw.split():
                    if part.isdigit() and 1 <= int(part) <= len(items):
                        route_filter.append(items[int(part) - 1][0])
            if route_filter:
                print(f"  → Filtering to: {', '.join(route_filter)}")
            else:
                print("  → All routes")

        # Step 4: pick direction filter
        print("\nFetching available directions...")
        departures_all = await fetch_departures(session, cluster_code, route_filter)
        available_directions = sorted({d["direction"] for d in departures_all if d["direction"]})
        direction_filter: list[str] = []
        if available_directions:
            print(f"Found {len(available_directions)} direction(s). Filter? (leave blank = all)")
            print("  0. All directions")
            for i, desc in enumerate(available_directions, 1):
                print(f"  {i}. {desc}")
            raw = input("Enter direction numbers separated by spaces (or 0/Enter for all): ").strip()
            if raw and raw != "0":
                for part in raw.split():
                    if part.isdigit() and 1 <= int(part) <= len(available_directions):
                        direction_filter.append(available_directions[int(part) - 1])
            if direction_filter:
                print(f"  → Filtering to: {', '.join(direction_filter)}")
            else:
                print("  → All directions")

        # Step 5: fetch and display departures
        print(f"\nFetching departures from {cluster_name}...")
        while True:
            departures = await fetch_departures(session, cluster_code, route_filter, direction_filter)
            if not departures:
                print("  No upcoming departures found.")
            else:
                print(f"\n{'#':<4} {'Time':<10} {'Line':<8} {'Direction':<30} {'Delay':>6}  {'RT':<5} {'Stop'}")
                print("-" * 80)
                for i, dep in enumerate(departures[:10], 1):
                    delay = dep["delay_minutes"]
                    delay_str = f"+{delay}m" if delay > 0 else ("  0m" if delay == 0 else f"{delay}m")
                    rt = "yes" if dep["realtime"] else "no"
                    print(
                        f"{i:<4} {fmt_time(dep['timestamp']):<10} {dep['line']:<8} "
                        f"{(dep['direction'] or ''):<30} {delay_str:>6}  {rt:<5} {dep['stop_name']}"
                    )

            again = input("\n[r] Refresh  [q] Quit: ").strip().lower()
            if again == "q":
                break


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBye!")
        sys.exit(0)
