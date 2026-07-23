# Mobilites-M for Home Assistant

A custom Home Assistant integration for the Grenoble public transit network (TAG / Transisere), using the open data API provided by [Mobilites-M](https://data.mobilites-m.fr).

This integration is not official and is not affiliated with Mobilites-M or the Grenoble metropolitan transit authority. I do not own the Mobilites-M logo included in this repository. This logo is the property of its respective owner and is used here solely for identification purposes within the Home Assistant UI.

## Features

- Real-time departure sensors for any stop cluster on the network
- Optional filtering by stop pole and direction
- One sensor per departure slot per direction (3 upcoming departures per direction by default)
- Sensors update every 60 seconds
- Prefixed line metadata on the `Line` and departure sensors: color, text color, short and long names, mode and type
- Short stop names on departure sensors for compact displays

## Installation

Install via [HACS](https://hacs.xyz) by adding this repository as a custom repository (category: Integration), or copy the `custom_components/mobilite_m` directory into your Home Assistant `config/custom_components` folder.

## Configuration

1. Go to Settings > Integrations > Add Integration and search for "Mobilites-M".
2. Enter the name of a stop or stop cluster (minimum 3 characters).
3. Select the matching cluster from the search results.
4. Optionally filter by stop pole and direction.

## Line metadata

The `Line`, `Departure`, `Departure +1` and `Departure +2` sensors expose route
metadata for the line they represent. All route-specific attributes use a
`line_` prefix to distinguish them from stop and departure information. Colors
are normalized with a leading `#` so they can be used directly in Home
Assistant templates or AWTRIX payloads.

```yaml
line_color: "#F5D24D"
line_text_color: "#000000"
line_short_name: C1
line_long_name: Grenoble Cité Jean Macé / Montbonnot-Saint-Martin Pré de l'Eau
line_mode: BUS
line_type: CHRONO
```

Departure sensors also expose `stop_short_name`, sourced from the stop data
already cached by the integration. The existing `stop_name` is preserved for
backward compatibility:

```yaml
stop_name: Grenoble, Chavant
stop_short_name: Chavant
```

## Requirements

- Home Assistant 2023.1.0 or later
- Network access to `data.mobilites-m.fr`
