#!/usr/bin/env python3
"""Regression test for geocode caching + last-known-coords fallback.

Before the fix, the weather plugin geocoded the configured city on *every*
refresh (every update_interval, on cache expiry) as a hard prerequisite to the
forecast call. When the Open-Meteo geocoding endpoint timed out, the whole
weather update aborted before any forecast was fetched, exponential backoff
disabled the widget for up to an hour, and a freshly-restarted plugin (no
cached weather) rendered blank — "weather widget fails to load".

The fix resolves coordinates once and caches them permanently across cycles and
restarts. Cities don't move, so a cached coordinate never goes stale and the
geocoding API is only ever called on a true cache miss.

Run with the core venv from a LEDMatrix checkout so manager's imports resolve:
    LEDMatrix/.venv/bin/python <thisfile>
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from manager import WeatherPlugin  # noqa: E402

logging.getLogger().addHandler(logging.NullHandler())


class FakeCache:
    """Minimal cache_manager double with controllable entry age."""

    def __init__(self):
        self.store = {}  # key -> (data, age_seconds)

    def set(self, key, data, ttl=None):
        self.store[key] = (data, 0)

    def get(self, key, max_age=300):
        if key not in self.store:
            return None
        data, age = self.store[key]
        return data if age <= max_age else None

    def age(self, key, seconds):
        """Pretend an existing entry was written `seconds` ago."""
        data, _ = self.store[key]
        self.store[key] = (data, seconds)


def _new_plugin(cache, override=None, coords=None):
    p = object.__new__(WeatherPlugin)
    p.plugin_id = "ledmatrix-weather"
    p.cache_manager = cache
    p.logger = logging.getLogger("test-weather")
    p._coord_override = override
    p._coords = coords
    p._last_error_hint = None
    return p


def _counting_geocode(result=(47.0, -122.0), fail_with=None):
    """Return a _geocode replacement that records how many times it ran."""
    calls = {"n": 0}

    def geocode(self, city, state, country):
        calls["n"] += 1
        if fail_with is not None:
            raise fail_with
        return result

    return geocode, calls


def check_resolves_once_then_reuses():
    cache = FakeCache()
    p = _new_plugin(cache)
    geocode, calls = _counting_geocode((47.0, -122.0))
    p._geocode = geocode.__get__(p, WeatherPlugin)

    first = p._resolve_coords("Federal Way", "WA", "US")
    second = p._resolve_coords("Federal Way", "WA", "US")

    fails = []
    if first != (47.0, -122.0) or second != (47.0, -122.0):
        fails.append(f"expected (47.0, -122.0) both times, got {first} / {second}")
    if calls["n"] != 1:
        fails.append(f"expected exactly 1 geocode call, got {calls['n']}")
    return fails


def check_reuses_across_restart():
    """A fresh instance (in-memory coords gone, as after a restart) reads the
    fresh cached coords without geocoding."""
    cache = FakeCache()
    seed = _new_plugin(cache)
    geocode, _ = _counting_geocode((47.0, -122.0))
    seed._geocode = geocode.__get__(seed, WeatherPlugin)
    seed._resolve_coords("Federal Way", "WA", "US")  # populate cache

    p = _new_plugin(cache)  # "restart": _coords is None
    geocode2, calls2 = _counting_geocode((1.0, 1.0))  # would differ if called
    p._geocode = geocode2.__get__(p, WeatherPlugin)
    coords = p._resolve_coords("Federal Way", "WA", "US")

    fails = []
    if coords != (47.0, -122.0):
        fails.append(f"expected cached (47.0, -122.0), got {coords}")
    if calls2["n"] != 0:
        fails.append(f"expected 0 geocode calls on restart, got {calls2['n']}")
    return fails


def check_cached_coords_never_expire():
    """THE headline regression: cities don't move, so a cached coordinate is
    reused no matter how old it is — the geocoding API is never called again
    once a location has been resolved."""
    cache = FakeCache()
    seed = _new_plugin(cache)
    geocode, _ = _counting_geocode((47.0, -122.0))
    seed._geocode = geocode.__get__(seed, WeatherPlugin)
    seed._resolve_coords("Federal Way", "WA", "US")  # populate cache

    coords_key = "ledmatrix-weather:Federal Way:WA:US:coords"
    cache.age(coords_key, 5 * 365 * 24 * 3600)  # 5 years old

    p = _new_plugin(cache)  # restart: in-memory coords gone
    geocode2, calls2 = _counting_geocode((1.0, 1.0))  # would differ if called
    p._geocode = geocode2.__get__(p, WeatherPlugin)
    coords = p._resolve_coords("Federal Way", "WA", "US")

    fails = []
    if coords != (47.0, -122.0):
        fails.append(f"expected cached (47.0, -122.0), got {coords}")
    if calls2["n"] != 0:
        fails.append(f"a 5-year-old cached coord must not re-geocode, got {calls2['n']} call(s)")
    return fails


def check_raises_when_never_resolved():
    """With no cached coords at all, a geocode failure still propagates so the
    normal error/backoff path runs."""
    cache = FakeCache()
    p = _new_plugin(cache)
    geocode, _ = _counting_geocode(fail_with=TimeoutError("read timed out"))
    p._geocode = geocode.__get__(p, WeatherPlugin)
    try:
        p._resolve_coords("Nowhere", "XX", "US")
    except TimeoutError:
        return []
    return ["expected TimeoutError to propagate when no coords were ever cached"]


def check_override_skips_geocoding():
    cache = FakeCache()
    p = _new_plugin(cache, override=(10.0, 20.0))
    geocode, calls = _counting_geocode((47.0, -122.0))
    p._geocode = geocode.__get__(p, WeatherPlugin)
    coords = p._resolve_coords("Federal Way", "WA", "US")

    fails = []
    if coords != (10.0, 20.0):
        fails.append(f"expected override (10.0, 20.0), got {coords}")
    if calls["n"] != 0:
        fails.append(f"override must skip geocoding, but geocoded {calls['n']}x")
    return fails


def check_parse_override():
    cases = [
        ((47.5, -122.3), (47.5, -122.3)),   # valid floats
        (("47.5", "-122.3"), (47.5, -122.3)),  # numeric strings
        ((None, -122.3), None),             # missing one
        (("", ""), None),                   # empty strings
        ((91.0, 0.0), None),                # lat out of range
        ((0.0, 181.0), None),               # lon out of range
        (("x", "y"), None),                 # non-numeric
    ]
    fails = []
    for (lat, lon), expected in cases:
        got = WeatherPlugin._parse_coord_override(lat, lon)
        if got != expected:
            fails.append(f"_parse_coord_override({lat!r}, {lon!r}) = {got}, expected {expected}")
    return fails


CHECKS = [
    ("resolve once then reuse", check_resolves_once_then_reuses),
    ("reuse across restart", check_reuses_across_restart),
    ("cached coords never expire", check_cached_coords_never_expire),
    ("raise when never resolved", check_raises_when_never_resolved),
    ("override skips geocoding", check_override_skips_geocoding),
    ("parse override validation", check_parse_override),
]


def main():
    total_fail = 0
    for name, fn in CHECKS:
        fails = fn()
        if fails:
            total_fail += 1
            print(f"FAIL {name}:")
            for f in fails:
                print(f"       {f}")
        else:
            print(f"PASS {name}")
    if total_fail:
        print(f"\n{total_fail} check(s) failed")
        sys.exit(1)
    print("\nGeocode-once-and-cache behavior holds")
    sys.exit(0)


if __name__ == "__main__":
    main()
