# Mobilites-M for Home Assistant

A custom Home Assistant integration for the Grenoble public transit network (TAG / Transisere), using the open data API provided by [Mobilites-M](https://data.mobilites-m.fr).

This integration is not official and is not affiliated with Mobilites-M or the Grenoble metropolitan transit authority. I do not own the Mobilites-M logo included in this repository. This logo is the property of its respective owner and is used here solely for identification purposes within the Home Assistant UI.

## Features

- Real-time departure sensors for any stop cluster on the network
- Optional filtering by stop pole and direction
- One sensor per departure slot per direction (3 upcoming departures per direction by default)
- Sensors update every 60 seconds

## Installation

Install via [HACS](https://hacs.xyz) by adding this repository as a custom repository (category: Integration), or copy the `custom_components/mobilite_m` directory into your Home Assistant `config/custom_components` folder.

## Configuration

1. Go to Settings > Integrations > Add Integration and search for "Mobilites-M".
2. Enter the name of a stop or stop cluster (minimum 3 characters).
3. Select the matching cluster from the search results.
4. Optionally filter by stop pole and direction.

## Requirements

- Home Assistant 2023.1.0 or later
- Network access to `data.mobilites-m.fr`
