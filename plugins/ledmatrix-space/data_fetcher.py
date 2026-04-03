"""
Data Fetcher for Space & Astronomy Tracker Plugin

Handles all data retrieval: ISS passes (skyfield), rocket launches (Launch Library 2),
planet visibility (skyfield), constellation visibility (skyfield), NASA APOD, and
ISS current position (Open Notify). Heavy computation is done locally via skyfield
to minimize API dependencies.
"""

import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple

import requests


# Lazy-load skyfield to avoid import errors during schema validation
_skyfield_loaded = False
_ts = None
_eph = None


def _ensure_skyfield():
    """Load skyfield timescale and ephemeris on first use."""
    global _skyfield_loaded, _ts, _eph
    if _skyfield_loaded:
        return True
    try:
        from skyfield.api import load
        _ts = load.timescale()
        # Use small ephemeris — ~17MB, covers 1900-2050
        _eph = load('de421.bsp')
        _skyfield_loaded = True
        return True
    except Exception:
        return False


# Region labels for ISS position
_REGIONS = [
    ((-90, -180, 0, -30), "South America"),
    ((0, -130, 50, -60), "North America"),
    ((50, -130, 90, -60), "Canada/Arctic"),
    ((0, -60, 90, 0), "Atlantic"),
    ((35, -15, 70, 40), "Europe"),
    ((-35, 10, 35, 55), "Africa"),
    ((10, 40, 70, 100), "Asia"),
    ((-10, 95, 50, 150), "East Asia"),
    ((-50, 110, -10, 180), "Australia"),
    ((-90, -180, 90, 180), "Pacific"),  # Catch-all
]


def _get_region(lat: float, lon: float) -> str:
    """Get a human-readable region name for a lat/lon."""
    for (min_lat, min_lon, max_lat, max_lon), name in _REGIONS:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return name
    return "Open Ocean"


class MockDataProvider:
    """Provides mock data for development/testing."""

    @staticmethod
    def get_iss_data() -> Dict[str, Any]:
        return {
            'next_pass': {
                'start_time': datetime.now(timezone.utc) + timedelta(hours=2, minutes=14),
                'duration_s': 360,
                'max_elevation': 62,
                'direction': 'SW→NE',
            },
            'is_overhead': False,
            'position': {'lat': 42.3, 'lon': -71.1, 'region': 'North America'},
            'people_in_space': [
                {'name': 'Oleg Kononenko', 'craft': 'ISS'},
                {'name': 'Nikolai Chub', 'craft': 'ISS'},
                {'name': 'Tracy Dyson', 'craft': 'ISS'},
                {'name': 'Mike Barratt', 'craft': 'ISS'},
                {'name': 'Jeanette Epps', 'craft': 'ISS'},
                {'name': 'Alexander Grebenkin', 'craft': 'ISS'},
                {'name': 'Matthew Dominick', 'craft': 'ISS'},
            ],
        }

    @staticmethod
    def get_launch_data() -> Dict[str, Any]:
        return {
            'name': 'Falcon 9 Block 5 | Starlink Group 12-5',
            'provider': 'SpaceX',
            'provider_abbr': 'SpX',
            'rocket': 'Falcon 9',
            'mission': 'Starlink Group 12-5',
            'pad': 'SLC-40, Cape Canaveral',
            'net': datetime.now(timezone.utc) + timedelta(days=3, hours=7, minutes=22),
            'status': 'Go',
            'status_id': 1,
        }

    @staticmethod
    def get_planets_data() -> List[Dict[str, Any]]:
        return [
            {'name': 'Mars', 'rise_time': '8:42 PM', 'set_time': '2:15 AM', 'magnitude': 0.8, 'visible': True},
            {'name': 'Jupiter', 'rise_time': '9:15 PM', 'set_time': '4:30 AM', 'magnitude': -2.1, 'visible': True},
            {'name': 'Saturn', 'rise_time': '11:02 PM', 'set_time': '5:45 AM', 'magnitude': 0.6, 'visible': True},
        ]

    @staticmethod
    def get_constellation_data() -> Dict[str, Any]:
        return {
            'name': 'Orion',
            'display_name': 'Orion',
            'season': 'winter',
        }

    @staticmethod
    def get_apod_data() -> Dict[str, Any]:
        return {
            'title': 'The Horsehead Nebula in Infrared',
            'date': '2026-04-03',
            'url': '',
            'media_type': 'image',
        }


class DataFetcher:
    """Fetches and caches space & astronomy data."""

    def __init__(self, config: Dict[str, Any], cache_manager, logger: Optional[logging.Logger] = None):
        self.config = config
        self.cache_manager = cache_manager
        self.logger = logger or logging.getLogger(__name__)

        self.lat = config.get('latitude', 0)
        self.lon = config.get('longitude', 0)
        self.elev = config.get('elevation_m', 0)
        self.nasa_key = config.get('nasa_api_key', '')
        self.min_elevation = config.get('iss', {}).get('min_elevation', 20)

        self._has_location = bool(self.lat or self.lon)
        self._use_mock = not self._has_location

        # Data cache
        self._iss_data: Optional[Dict] = None
        self._launch_data: Optional[Dict] = None
        self._planets_data: Optional[List] = None
        self._constellation_data: Optional[Dict] = None
        self._apod_data: Optional[Dict] = None
        self._last_iss_fetch = 0
        self._last_launch_fetch = 0
        self._last_sky_fetch = 0
        self._last_apod_fetch = 0

        # Plugin asset directory for constellations.json
        self._plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self._constellation_catalog = self._load_constellation_catalog()

        if self._use_mock:
            self.logger.info("No location configured — using mock data")
        else:
            self.logger.info(f"Location: {self.lat}, {self.lon} (elev {self.elev}m)")
            if not _ensure_skyfield():
                self.logger.warning("skyfield not available — ISS passes and planet data will use mock data")

    def _load_constellation_catalog(self) -> Dict:
        """Load constellation patterns from assets."""
        path = os.path.join(self._plugin_dir, 'assets', 'constellations.json')
        try:
            with open(path) as f:
                data = json.load(f)
            return data.get('constellations', {})
        except Exception as e:
            self.logger.warning(f"Could not load constellations.json: {e}")
            return {}

    # ── ISS Data ────────────────────────────────────────────────

    def fetch_iss_data(self, force: bool = False) -> None:
        """Fetch ISS pass predictions and current position."""
        now = time.time()
        if not force and now - self._last_iss_fetch < 300:  # 5 min cache
            return

        if self._use_mock or not _skyfield_loaded:
            self._iss_data = MockDataProvider.get_iss_data()
            self._last_iss_fetch = now
            return

        try:
            # Fetch current ISS position from Open Notify
            position = self._fetch_iss_position()

            # Compute next pass using skyfield
            next_pass = self._compute_next_iss_pass()

            # Check if ISS is overhead right now
            is_overhead = self._check_iss_overhead(next_pass)

            # Fetch people in space
            people = self._fetch_people_in_space()

            self._iss_data = {
                'next_pass': next_pass,
                'is_overhead': is_overhead,
                'position': position,
                'people_in_space': people,
            }
            self._last_iss_fetch = now
        except Exception as e:
            self.logger.error(f"Error fetching ISS data: {e}", exc_info=True)
            if not self._iss_data:
                self._iss_data = MockDataProvider.get_iss_data()

    def _fetch_iss_position(self) -> Dict[str, Any]:
        """Get current ISS position from Open Notify."""
        try:
            resp = requests.get('http://api.open-notify.org/iss-now.json', timeout=10)
            resp.raise_for_status()
            data = resp.json()
            lat = float(data['iss_position']['latitude'])
            lon = float(data['iss_position']['longitude'])
            return {'lat': lat, 'lon': lon, 'region': _get_region(lat, lon)}
        except Exception as e:
            self.logger.debug(f"ISS position fetch failed: {e}")
            return {'lat': 0, 'lon': 0, 'region': 'Unknown'}

    def _compute_next_iss_pass(self) -> Optional[Dict[str, Any]]:
        """Compute next visible ISS pass using skyfield."""
        if not _skyfield_loaded:
            return None
        try:
            from skyfield.api import load, wgs84, EarthSatellite

            # Load ISS TLE
            stations_url = 'https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE'
            try:
                sats = load.tle_file(stations_url, reload=True, filename='iss-tle.txt')
            except Exception:
                # Fallback if TLE download fails
                self.logger.warning("Could not download ISS TLE, using cached or skipping")
                return None

            if not sats:
                return None
            iss = sats[0]

            location = wgs84.latlon(self.lat, self.lon, elevation_m=self.elev)
            t0 = _ts.now()
            t1 = _ts.from_datetime(datetime.now(timezone.utc) + timedelta(days=7))

            # Find events (rise=0, culminate=1, set=2)
            t_events, events = iss.find_events(location, t0, t1, altitude_degrees=self.min_elevation)

            for i, (t, event) in enumerate(zip(t_events, events)):
                if event == 0:  # Rise
                    # Find corresponding culmination and set
                    start_time = t.utc_datetime()
                    max_elev = self.min_elevation
                    end_time = start_time + timedelta(minutes=6)  # Default

                    for j in range(i+1, min(i+3, len(events))):
                        if events[j] == 1:  # Culmination
                            diff = iss - location
                            alt, az, _ = diff.at(t_events[j]).altaz()
                            max_elev = alt.degrees
                        elif events[j] == 2:  # Set
                            end_time = t_events[j].utc_datetime()
                            break

                    duration = (end_time - start_time).total_seconds()

                    # Compute direction
                    diff = iss - location
                    alt_rise, az_rise, _ = diff.at(t).altaz()
                    direction = self._azimuth_to_compass(az_rise.degrees)

                    return {
                        'start_time': start_time,
                        'end_time': end_time,
                        'duration_s': int(duration),
                        'max_elevation': int(max_elev),
                        'direction': direction,
                    }

            return None
        except Exception as e:
            self.logger.error(f"ISS pass computation error: {e}", exc_info=True)
            return None

    def _check_iss_overhead(self, next_pass: Optional[Dict]) -> bool:
        """Check if ISS is currently overhead."""
        if not next_pass or 'start_time' not in next_pass:
            return False
        now = datetime.now(timezone.utc)
        start = next_pass['start_time']
        end = next_pass.get('end_time', start + timedelta(seconds=next_pass.get('duration_s', 360)))
        return start <= now <= end

    def _fetch_people_in_space(self) -> List[Dict[str, str]]:
        """Get current people in space from Open Notify."""
        try:
            resp = requests.get('http://api.open-notify.org/astros.json', timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get('people', [])
        except Exception:
            return []

    @staticmethod
    def _azimuth_to_compass(az: float) -> str:
        """Convert azimuth degrees to compass direction string."""
        dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
        idx = int((az + 11.25) / 22.5) % 16
        return dirs[idx]

    # ── Launch Data ─────────────────────────────────────────────

    def fetch_launch_data(self, force: bool = False) -> None:
        """Fetch next upcoming launch from Launch Library 2."""
        now = time.time()
        if not force and now - self._last_launch_fetch < 1800:  # 30 min cache
            return

        try:
            resp = requests.get(
                'https://ll.thespacedevs.com/2.3.0/launches/upcoming/',
                params={'limit': 1, 'mode': 'list'},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get('results', [])
            if not results:
                self._launch_data = MockDataProvider.get_launch_data()
                self._last_launch_fetch = now
                return

            launch = results[0]
            provider = launch.get('launch_service_provider', {})
            provider_name = provider.get('name', 'Unknown')
            rocket = launch.get('rocket', {}).get('configuration', {})
            mission = launch.get('mission') or {}
            pad = launch.get('pad', {})
            status = launch.get('status', {})

            # Abbreviate provider
            abbr_map = {
                'SpaceX': 'SpX', 'NASA': 'NASA', 'United Launch Alliance': 'ULA',
                'Rocket Lab': 'RLAB', 'Blue Origin': 'BO', 'ISRO': 'ISRO',
                'Roscosmos': 'RSCM', 'China Aerospace': 'CNSA',
            }
            provider_abbr = abbr_map.get(provider_name, provider_name[:4].upper())

            # Parse NET time
            net_str = launch.get('net', '')
            try:
                net = datetime.fromisoformat(net_str.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                net = datetime.now(timezone.utc) + timedelta(days=1)

            pad_name = pad.get('name', '')
            pad_location = pad.get('location', {}).get('name', '')
            pad_display = f"{pad_name}, {pad_location}" if pad_name else pad_location

            self._launch_data = {
                'name': launch.get('name', 'Unknown Launch'),
                'provider': provider_name,
                'provider_abbr': provider_abbr,
                'rocket': rocket.get('name', 'Unknown'),
                'mission': mission.get('name', launch.get('name', '')),
                'pad': pad_display,
                'net': net,
                'status': status.get('name', 'TBD'),
                'status_id': status.get('id', 2),
            }
            self._last_launch_fetch = now
            self.logger.info(f"Next launch: {self._launch_data['name']} at {net}")
        except Exception as e:
            self.logger.error(f"Error fetching launch data: {e}", exc_info=True)
            if not self._launch_data:
                self._launch_data = MockDataProvider.get_launch_data()
            self._last_launch_fetch = now

    # ── Night Sky (Planets + Constellations) ────────────────────

    def fetch_night_sky_data(self, force: bool = False) -> None:
        """Compute visible planets and constellations."""
        now = time.time()
        if not force and now - self._last_sky_fetch < 3600:  # 1 hour cache
            return

        if self._use_mock or not _skyfield_loaded:
            self._planets_data = MockDataProvider.get_planets_data()
            self._constellation_data = MockDataProvider.get_constellation_data()
            self._last_sky_fetch = now
            return

        try:
            self._planets_data = self._compute_visible_planets()
            self._constellation_data = self._pick_constellation()
            self._last_sky_fetch = now
        except Exception as e:
            self.logger.error(f"Error computing night sky: {e}", exc_info=True)
            if not self._planets_data:
                self._planets_data = MockDataProvider.get_planets_data()
            if not self._constellation_data:
                self._constellation_data = MockDataProvider.get_constellation_data()

    def _compute_visible_planets(self) -> List[Dict[str, Any]]:
        """Compute which planets are visible tonight."""
        if not _skyfield_loaded:
            return []
        try:
            from skyfield.api import wgs84
            from skyfield.almanac import find_discrete, risings_and_settings

            location = wgs84.latlon(self.lat, self.lon, elevation_m=self.elev)
            earth = _eph['earth']
            observer = earth + location

            planet_names = {
                'mercury': 'Mercury', 'venus': 'Venus', 'mars': 'Mars',
                'jupiter barycenter': 'Jupiter', 'saturn barycenter': 'Saturn',
            }

            now = datetime.now(timezone.utc)
            t_now = _ts.from_datetime(now)
            visible = []

            for body_name, display_name in planet_names.items():
                try:
                    planet = _eph[body_name]
                    astrometric = observer.at(t_now).observe(planet)
                    alt, az, _ = astrometric.apparent().altaz()

                    # Check if above horizon or will rise tonight
                    # Compute rise/set for next 24h
                    t0 = t_now
                    t1 = _ts.from_datetime(now + timedelta(hours=24))

                    f = risings_and_settings(_eph, planet, location)
                    times, events = find_discrete(t0, t1, f)

                    rise_time_str = None
                    set_time_str = None
                    for t, event in zip(times, events):
                        local_dt = t.utc_datetime().astimezone()
                        time_str = local_dt.strftime('%-I:%M %p')
                        if event and not rise_time_str:
                            rise_time_str = time_str
                        elif not event and not set_time_str:
                            set_time_str = time_str

                    is_visible = alt.degrees > 5 or rise_time_str is not None

                    if is_visible:
                        # Get apparent magnitude (approximate)
                        mag = self._approx_magnitude(display_name)
                        visible.append({
                            'name': display_name,
                            'rise_time': rise_time_str or 'Up',
                            'set_time': set_time_str or '',
                            'magnitude': mag,
                            'altitude': alt.degrees,
                            'visible': True,
                        })
                except Exception as e:
                    self.logger.debug(f"Error computing {display_name}: {e}")

            # Sort by brightness (lower magnitude = brighter)
            visible.sort(key=lambda p: p['magnitude'])
            return visible
        except Exception as e:
            self.logger.error(f"Planet computation error: {e}", exc_info=True)
            return []

    @staticmethod
    def _approx_magnitude(planet: str) -> float:
        """Approximate typical apparent magnitudes."""
        mags = {'Venus': -4.0, 'Jupiter': -2.1, 'Mars': 0.8, 'Saturn': 0.6, 'Mercury': 0.5}
        return mags.get(planet, 2.0)

    def _pick_constellation(self) -> Optional[Dict[str, Any]]:
        """Pick a prominent constellation for the current season."""
        if not self._constellation_catalog:
            return None

        month = datetime.now().month
        season_map = {
            12: 'winter', 1: 'winter', 2: 'winter',
            3: 'spring', 4: 'spring', 5: 'spring',
            6: 'summer', 7: 'summer', 8: 'summer',
            9: 'fall', 10: 'fall', 11: 'fall',
        }
        current_season = season_map.get(month, 'winter')

        # Pick constellations matching current season, or all-season ones
        candidates = []
        for key, data in self._constellation_catalog.items():
            if data.get('season') == current_season:
                candidates.append(data)

        if not candidates:
            # Fallback to any constellation
            candidates = list(self._constellation_catalog.values())

        if candidates:
            # Rotate through candidates based on day of month
            idx = datetime.now().day % len(candidates)
            return candidates[idx]
        return None

    # ── APOD ────────────────────────────────────────────────────

    def fetch_apod_data(self, force: bool = False) -> None:
        """Fetch NASA Astronomy Picture of the Day."""
        now = time.time()
        if not force and now - self._last_apod_fetch < 86400:  # 24 hour cache
            return

        api_key = self.nasa_key or 'DEMO_KEY'
        try:
            resp = requests.get(
                'https://api.nasa.gov/planetary/apod',
                params={'api_key': api_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            self._apod_data = {
                'title': data.get('title', 'Astronomy Picture of the Day'),
                'date': data.get('date', ''),
                'url': data.get('url', ''),
                'media_type': data.get('media_type', 'image'),
            }
            self._last_apod_fetch = now
        except Exception as e:
            self.logger.error(f"APOD fetch error: {e}")
            if not self._apod_data:
                self._apod_data = MockDataProvider.get_apod_data()

    # ── Public Accessors ────────────────────────────────────────

    def get_iss_data(self) -> Dict[str, Any]:
        return self._iss_data or MockDataProvider.get_iss_data()

    def get_launch_data(self) -> Dict[str, Any]:
        return self._launch_data or MockDataProvider.get_launch_data()

    def get_planets_data(self) -> List[Dict[str, Any]]:
        return self._planets_data or MockDataProvider.get_planets_data()

    def get_constellation_data(self) -> Optional[Dict[str, Any]]:
        return self._constellation_data or MockDataProvider.get_constellation_data()

    def get_apod_data(self) -> Dict[str, Any]:
        return self._apod_data or MockDataProvider.get_apod_data()
