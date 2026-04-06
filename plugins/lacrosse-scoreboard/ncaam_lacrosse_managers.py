import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pytz

from lacrosse import Lacrosse, LacrosseLive
from sports import SportsRecent, SportsUpcoming

# Constants
ESPN_NCAAMLAX_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/lacrosse/mens-college-lacrosse/scoreboard"
)


class BaseNCAAMLacrosseManager(Lacrosse):
    """Base class for NCAA Men's Lacrosse managers with common functionality."""

    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _shared_data = None
    _last_shared_update = 0
    _processed_games_cache = {}  # Cache for processed game data
    _processed_games_timestamp = 0

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
    ):
        self.logger = logging.getLogger("NCAAMLAX")
        super().__init__(
            config=config,
            display_manager=display_manager,
            cache_manager=cache_manager,
            logger=self.logger,
            sport_key="ncaam_lacrosse",
        )

        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("lacrosse_recent", False)
        self.upcoming_enabled = display_modes.get("lacrosse_upcoming", False)
        self.live_enabled = display_modes.get("lacrosse_live", False)
        self.league = "mens-college-lacrosse"

        self.logger.info(
            f"Initialized NCAAMLacrosse manager with display dimensions: {self.display_width}x{self.display_height}"
        )
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(
            f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}"
        )

    def _fetch_ncaa_lacrosse_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetches the full season schedule for NCAAM Lacrosse, caches it, and then filters
        for relevant games based on the current configuration.

        NCAA men's lacrosse season runs roughly January through May.
        """
        now = datetime.now(pytz.utc)
        season_year = now.year
        # After the NCAA championship (late May), roll to the next season year for caching
        if now.month >= 7:
            season_year = now.year + 1
        datestring = f"{season_year}0101-{season_year}0601"
        cache_key = f"ncaa_mens_lacrosse_schedule_{season_year}"

        if use_cache:
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                if isinstance(cached_data, dict) and "events" in cached_data:
                    self.logger.info(f"Using cached schedule for {season_year}")
                    return cached_data
                elif isinstance(cached_data, list):
                    self.logger.info(
                        f"Using cached schedule for {season_year} (legacy format)"
                    )
                    return {"events": cached_data}
                else:
                    self.logger.warning(
                        f"Invalid cached data format for {season_year}: {type(cached_data)}"
                    )
                    self.cache_manager.clear_cache(cache_key)

        self.logger.info(
            f"Fetching full {season_year} season schedule from ESPN API..."
        )
        self.logger.info(
            f"Starting background fetch for {season_year} season schedule..."
        )

        def fetch_callback(result):
            """Callback when background fetch completes."""
            if result.success:
                self.logger.info(
                    f"Background fetch completed for {season_year}: {len(result.data.get('events'))} events"
                )
            else:
                self.logger.error(
                    f"Background fetch failed for {season_year}: {result.error}"
                )

            if season_year in self.background_fetch_requests:
                del self.background_fetch_requests[season_year]

        background_config = self.mode_config.get("background_service", {})
        timeout = background_config.get("request_timeout", 30)
        max_retries = background_config.get("max_retries", 3)
        priority = background_config.get("priority", 2)

        request_id = self.background_service.submit_fetch_request(
            sport="ncaa_mens_lacrosse",
            year=season_year,
            url=ESPN_NCAAMLAX_SCOREBOARD_URL,
            cache_key=cache_key,
            params={"dates": datestring, "limit": 1000},
            headers=self.headers,
            timeout=timeout,
            max_retries=max_retries,
            priority=priority,
            callback=fetch_callback,
        )

        self.background_fetch_requests[season_year] = request_id

        partial_data = self._get_weeks_data()
        if partial_data:
            return partial_data
        return None

    def _fetch_data(self) -> Optional[Dict]:
        """Fetch data using shared data mechanism or direct fetch for live."""
        if isinstance(self, NCAAMLacrosseLiveManager):
            return self._fetch_todays_games()
        else:
            return self._fetch_ncaa_lacrosse_api_data(use_cache=True)


class NCAAMLacrosseLiveManager(BaseNCAAMLacrosseManager, LacrosseLive):
    """Manager for live NCAA Men's Lacrosse games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("NCAAMLacrosseLiveManager")

        # Initialize with test game only if test mode is enabled
        if self.test_mode:
            self.current_game = {
                "id": "401712345",
                "home_abbr": "MD",
                "away_abbr": "JHU",
                "home_score": "7",
                "away_score": "5",
                "period": 2,
                "period_text": "Q2",
                "home_id": "120",
                "away_id": "228",
                "clock": "08:42",
                "home_logo_path": Path(self.logo_dir, "MD.png"),
                "away_logo_path": Path(self.logo_dir, "JHU.png"),
                "game_time": "7:00 PM",
                "game_date": "Apr 12",
                "is_live": True,
                "is_final": False,
                "is_upcoming": False,
            }
            self.live_games = [self.current_game]
            self.logger.info(
                "Initialized NCAAMLacrosseLiveManager with test game: MD vs JHU"
            )
        else:
            self.logger.info("Initialized NCAAMLacrosseLiveManager in live mode")


class NCAAMLacrosseRecentManager(BaseNCAAMLacrosseManager, SportsRecent):
    """Manager for recently completed NCAA Men's Lacrosse games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("NCAAMLacrosseRecentManager")
        self.logger.info(
            f"Initialized NCAAMLacrosseRecentManager with {len(self.favorite_teams)} favorite teams"
        )


class NCAAMLacrosseUpcomingManager(BaseNCAAMLacrosseManager, SportsUpcoming):
    """Manager for upcoming NCAA Men's Lacrosse games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("NCAAMLacrosseUpcomingManager")
        self.logger.info(
            f"Initialized NCAAMLacrosseUpcomingManager with {len(self.favorite_teams)} favorite teams"
        )
