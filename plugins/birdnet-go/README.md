# BirdNET-Go Plugin

Subscribe to a [BirdNET-Go](https://github.com/tphakala/birdnet-go) MQTT feed and display the most recently identified bird on your LED matrix — common name, confidence, time since detection, and a species image fetched from BirdNET-Go's built-in media API.

## Features

- MQTT subscription with auto-reconnect and exponential backoff
- Two-line layout: scrolling bird name + confidence/time-ago
- Species images pulled on demand from BirdNET-Go and cached for 30 days
- Three display modes: `rotation`, `interrupt`, or `both`
- Configurable confidence threshold to filter noisy detections
- Defensive payload parsing — tolerates `CommonName` / `commonName` / `common_name` and decimal-or-percent confidence
- Optional field mapping for unusual BirdNET-Go forks

## Configuration

Minimum example:

```json
{
  "enabled": true,
  "mqtt": {
    "host": "192.168.1.10",
    "port": 1883,
    "username": "user",
    "password": "pass",
    "topic": "birdnet/detections"
  },
  "birdnet_api": {
    "base_url": "http://birdnet-go.local:8080"
  },
  "display": {
    "mode": "both",
    "min_confidence": 0.6
  }
}
```

Full option reference: see `config_schema.json`.

### Display modes

- **`rotation`** — plugin renders the last detected bird during its rotation slot; nothing pops up mid-rotation.
- **`interrupt`** — plugin stays silent during rotation; new detections interrupt the current display for `interrupt_duration` seconds.
- **`both`** (default) — rotation slot shows the last bird, *and* new detections interrupt immediately.

If more than `stale_after_minutes` pass without a detection, the rotation slot shows "No recent birds" instead.

## How to enable BirdNET-Go MQTT

In BirdNET-Go's settings, enable MQTT under *Integrations* and set a topic (e.g. `birdnet/detections`). Point this plugin's `mqtt.host` at your broker and use the same topic. The plugin tolerates the common payload shapes; if your fork uses different JSON keys, map them via `field_mapping`.

## How images are fetched

The plugin calls `GET {birdnet_api.base_url}/api/v2/media/species-image?name=<scientific_name>` in the background after each new detection. Results are cached in memory and in the LEDMatrix cache for 30 days, keyed by scientific name. Failed lookups are remembered for the session so we don't hammer the API.

If `birdnet_api.base_url` is empty or the endpoint returns non-200, the plugin falls back to text-only layout.

## Troubleshooting

- **Nothing appears** — verify broker connectivity (`mosquitto_sub -h <host> -t 'birdnet/#' -v`) and that `min_confidence` isn't set too high. Check plugin logs for "Dropping low-confidence detection".
- **Name but no image** — check `birdnet_api.base_url` is reachable from the LEDMatrix host. `curl "<base_url>/api/v2/media/species-image?name=Cardinalis%20cardinalis"` should return an image.
- **Wrong field parsed** — enable debug logging to see the raw payload, then override the relevant key under `field_mapping`.

## Display modes supported

`birdnet_go`
