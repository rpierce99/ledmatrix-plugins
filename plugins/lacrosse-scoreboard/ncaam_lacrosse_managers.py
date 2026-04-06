import logging
from pathlib import Path
from typing import Any, Dict, Optional

from lacrosse import Lacrosse, LacrosseLive
from sports import SportsRecent, SportsUpcoming

# Constants
ESPN_NCAAMLAX_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/lacrosse/mens-college-lacrosse/scoreboard"
)


class BaseNCAAMLacrosseManager(Lacrosse):
    """Base class for NCAA Men's Lacrosse managers with common functionality."""

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

        # Check display modes to determine what data to fetch.
        # Keys match the adapter output in manager.py::_adapt_config_for_manager
        # and the plain "live"/"recent"/"upcoming" names used in
        # config_schema.json.
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("recent", False)
        self.upcoming_enabled = display_modes.get("upcoming", False)
        self.live_enabled = display_modes.get("live", False)
        self.league = "mens-college-lacrosse"

        self.logger.info(
            f"Initialized NCAAMLacrosse manager with display dimensions: {self.display_width}x{self.display_height}"
        )
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(
            f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}"
        )

    def _fetch_ncaa_lacrosse_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """Fetch the men's NCAA lacrosse season schedule (January through May)."""
        return self._fetch_season_schedule(
            sport="ncaa_mens_lacrosse",
            cache_key_prefix="ncaa_mens_lacrosse_schedule",
            scoreboard_url=ESPN_NCAAMLAX_SCOREBOARD_URL,
            season_start_mmdd="0101",
            use_cache=use_cache,
        )

    def _fetch_data(self) -> Optional[Dict]:
        """Default fetch: pull the cached season schedule.

        Live managers override this to query only today's games.
        """
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

    def _fetch_data(self) -> Optional[Dict]:
        """Live fetch: pull today's games directly rather than the full season."""
        return self._fetch_todays_games()


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
