#!/usr/bin/env python3
"""Regression test: the overhead / live-priority trigger must be governed by
``proximity_distance_miles`` alone, independent of ``map_radius_miles``.

Reproduces the eave-board bug where ``map_radius_miles = 1.0`` (a tight map view)
silently capped the overhead radius, so a plane 2 mi out — well inside the 3 mi
``proximity_distance_miles`` — never triggered the live overhead view because the
candidate pool (``aircraft_data``) had already been filtered to the map radius.

Runs without the full display stack: it builds a bare manager via
``object.__new__`` and sets only the attributes the proximity logic touches.

Exit 0 = PASS, 1 = FAIL.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from manager import FlightTrackerPlugin  # noqa: E402


def _make_manager(map_radius, proximity_radius):
    """A FlightTrackerPlugin with only the proximity-state attributes set."""
    m = object.__new__(FlightTrackerPlugin)
    m.map_radius_miles = map_radius
    m.proximity_distance_miles = proximity_radius
    m.proximity_enabled = True
    m.live_priority_enabled = True
    # State machine fields read/written by _evaluate_proximity.
    m._lock_icao = None
    m._lock_aircraft = None
    m._lock_start = 0.0
    m._cooldown_until = 0.0
    m.proximity_duration = 30.0
    m.proximity_cooldown = 30.0
    # aircraft_data = map_radius subset (empty: the plane is outside 1 mi).
    # all_aircraft_data = full fetched pool (holds the 2 mi plane).
    plane = {'icao': 'ABC123', 'callsign': 'TEST123', 'distance_miles': 2.0,
             'last_seen': time.time(), 'lat': 47.37, 'lon': -122.29}
    m.aircraft_data = {}
    m.all_aircraft_data = {'ABC123': plane}
    return m, plane


def main():
    failures = []

    # 1. Plane at 2 mi, map_radius 1 mi, proximity 3 mi: must be found.
    m, plane = _make_manager(map_radius=1.0, proximity_radius=3.0)
    icao, ac = m._closest_in_radius()
    if icao != 'ABC123' or ac is not plane:
        failures.append(
            f"_closest_in_radius() ignored a plane at 2mi inside the 3mi proximity "
            f"radius because map_radius is 1mi (got icao={icao!r})")

    # 2. The full state machine must lock onto it (live content == True).
    m, plane = _make_manager(map_radius=1.0, proximity_radius=3.0)
    if not m._evaluate_proximity():
        failures.append("_evaluate_proximity() did not lock onto the 2mi overhead plane")
    elif m._lock_aircraft is not plane:
        failures.append("_evaluate_proximity() locked, but _lock_aircraft is wrong")

    # 3. proximity_distance_miles is still respected: a plane beyond it must NOT trigger.
    m, plane = _make_manager(map_radius=1.0, proximity_radius=3.0)
    plane['distance_miles'] = 5.0  # outside the 3mi proximity radius
    icao, ac = m._closest_in_radius()
    if icao is not None:
        failures.append(
            f"_closest_in_radius() returned a plane at 5mi outside the 3mi proximity "
            f"radius (got icao={icao!r})")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: overhead trigger honors proximity_distance_miles independent of map_radius_miles")
    return 0


if __name__ == '__main__':
    sys.exit(main())
