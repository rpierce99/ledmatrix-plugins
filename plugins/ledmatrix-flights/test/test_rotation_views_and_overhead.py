#!/usr/bin/env python3
"""
Regression tests for configurable rotation views + overhead live-priority preempt.

Covers:
  - `_get_available_modes`: legacy single slot, per-view rotation slots, the
    "none" (empty) case, invalid-view filtering, and the overhead live slot.
  - The proximity latch: an aircraft entering the radius makes the plugin "live"
    for the full proximity window, even after it leaves the radius.
  - `display()` returning False (skip) for an inactive live slot, an empty
    rotation slot, and an unknown slot name.

Run with the core venv:
    .venv/bin/python plugins/ledmatrix-flights/test/test_rotation_views_and_overhead.py
"""

import logging
import os
import sys
import time
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

# Stub the core's BasePlugin so `manager` imports without the full LEDMatrix core
# tree on the path. We bypass __init__ in these tests, so a no-op base is enough.
if "src.plugin_system.base_plugin" not in sys.modules:
    src_mod = types.ModuleType("src")
    plugin_system_mod = types.ModuleType("src.plugin_system")
    base_plugin_mod = types.ModuleType("src.plugin_system.base_plugin")

    class _StubBasePlugin:  # pragma: no cover - trivial stub
        def __init__(self, *args, **kwargs):
            pass

    base_plugin_mod.BasePlugin = _StubBasePlugin
    src_mod.plugin_system = plugin_system_mod
    plugin_system_mod.base_plugin = base_plugin_mod
    sys.modules["src"] = src_mod
    sys.modules["src.plugin_system"] = plugin_system_mod
    sys.modules["src.plugin_system.base_plugin"] = base_plugin_mod

logging.basicConfig(level=logging.CRITICAL)

from manager import FlightTrackerPlugin  # noqa: E402


def make_plugin(config):
    """Build a FlightTrackerPlugin with only the attributes the units-under-test
    need, bypassing the heavy real __init__ (which requires display/cache managers).
    """
    p = FlightTrackerPlugin.__new__(FlightTrackerPlugin)
    p.config = config
    p.logger = logging.getLogger("flight-test")

    pc = config.get("proximity_alert", {})
    p.proximity_enabled = pc.get("enabled", True)
    p.proximity_distance_miles = pc.get("distance_miles", 0.1)
    p.proximity_duration = pc.get("duration_seconds", 30)
    p.live_priority_enabled = config.get("live_priority", False)

    p.display_mode = config.get("display_mode", "auto")
    p.proximity_triggered_time = None
    p._proximity_aircraft = None
    p._active_overhead_aircraft = None

    p.aircraft_data = {}
    p.all_aircraft_data = {}
    p.tracked_flight_data = {}

    # display() preamble attrs
    p._last_displayed_time = 0.0
    p._display_idle_threshold = 30.0
    p.fr24_enrichment = False
    p.last_fr24_enrichment = 0.0

    p.modes = p._get_available_modes()
    return p


def _aircraft(distance_miles):
    return {"icao": "abc123", "callsign": "TEST123", "distance_miles": distance_miles}


# ---------------------------------------------------------------------------
# _get_available_modes
# ---------------------------------------------------------------------------

def test_legacy_single_slot():
    p = make_plugin({})  # no rotation_views, live_priority off
    assert p.modes == ["flight_tracker"], p.modes


def test_legacy_with_overhead_priority():
    p = make_plugin({"live_priority": True})
    assert p.modes == ["flight_tracker", "flight_tracker_live"], p.modes


def test_rotation_views_subset_preserves_order():
    p = make_plugin({"rotation_views": ["stats", "map"]})
    assert p.modes == ["flight_tracker_stats", "flight_tracker_map"], p.modes


def test_rotation_views_filters_invalid_and_overhead():
    # 'overhead' and unknown views are not valid rotation views.
    p = make_plugin({"rotation_views": ["map", "overhead", "bogus", "area"]})
    assert p.modes == ["flight_tracker_map", "flight_tracker_area"], p.modes


def test_none_rotation_with_overhead_only():
    p = make_plugin({"rotation_views": [], "live_priority": True})
    assert p.modes == ["flight_tracker_live"], p.modes


def test_none_rotation_without_priority_is_empty():
    p = make_plugin({"rotation_views": []})
    assert p.modes == [], p.modes


def test_rotation_views_plus_overhead():
    p = make_plugin({"rotation_views": ["map", "stats"], "live_priority": True})
    assert p.modes == [
        "flight_tracker_map",
        "flight_tracker_stats",
        "flight_tracker_live",
    ], p.modes


# ---------------------------------------------------------------------------
# Proximity latch / live content
# ---------------------------------------------------------------------------

def test_proximity_latch_holds_for_window():
    p = make_plugin({"live_priority": True,
                     "proximity_alert": {"distance_miles": 0.1, "duration_seconds": 20}})
    # Aircraft enters the radius.
    p.aircraft_data = {"abc123": _aircraft(0.05)}
    assert p.has_live_priority() is True
    assert p.has_live_content() is True
    assert p.get_live_modes() == ["flight_tracker_live"]
    assert p._proximity_aircraft is not None

    # Aircraft leaves the radius but we are still inside the alert window:
    # the latch keeps the plugin "live".
    p.aircraft_data = {"abc123": _aircraft(5.0)}
    assert p.has_live_content() is True

    # Once the window elapses, live content clears.
    p.proximity_triggered_time = time.time() - 999
    assert p.has_live_content() is False
    assert p.get_live_modes() == []


def test_live_priority_requires_optin():
    # Without live_priority, a close aircraft never preempts the rotation.
    p = make_plugin({"proximity_alert": {"distance_miles": 0.1}})
    p.aircraft_data = {"abc123": _aircraft(0.05)}
    assert p.has_live_priority() is False
    assert p.get_live_modes() == []


def test_proximity_disabled_has_no_live_content():
    p = make_plugin({"live_priority": True, "proximity_alert": {"enabled": False}})
    p.aircraft_data = {"abc123": _aircraft(0.01)}
    assert p.has_live_content() is False
    assert p.has_live_priority() is False


# ---------------------------------------------------------------------------
# display() skip behavior (returns False -> framework rotates on)
# ---------------------------------------------------------------------------

def test_display_live_slot_skips_when_inactive():
    p = make_plugin({"live_priority": True})
    p.aircraft_data = {"abc123": _aircraft(5.0)}  # nothing overhead
    assert p.display(display_mode="flight_tracker_live") is False


def test_display_empty_rotation_view_skips():
    p = make_plugin({"rotation_views": ["map"]})
    p.aircraft_data = {}  # empty map -> skip
    assert p.display(display_mode="flight_tracker_map") is False


def test_display_unknown_slot_skips():
    p = make_plugin({"rotation_views": []})
    # The plugin-id fallback slot the core injects when modes is empty.
    assert p.display(display_mode="ledmatrix-flights") is False


# ---------------------------------------------------------------------------
# _view_has_content
# ---------------------------------------------------------------------------

def test_view_has_content():
    p = make_plugin({})
    assert p._view_has_content("stats") is True  # stats always has something
    assert p._view_has_content("map") is False
    assert p._view_has_content("flight_tracking") is False
    p.aircraft_data = {"abc123": _aircraft(2.0)}
    assert p._view_has_content("map") is True
    assert p._view_has_content("area") is True


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # pragma: no cover
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return failures == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
