import logging
from pathlib import Path
from typing import Any, Dict, Optional

from lacrosse import Lacrosse, LacrosseLive
from sports import SportsRecent, SportsUpcoming

# Constants
ESPN_NCAAWLAX_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/lacrosse/womens-college-lacrosse/scoreboard"
)


class BaseNCAAWLacrosseManager(Lacrosse):
    """Base class for NCAA Women's Lacrosse managers with common functionality."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
    ):
        self.logger = logging.getLogger("NCAAWLAX")
        super().__init__(
            config=config,
            display_manager=display_manager,
            cache_manager=cache_manager,
            logger=self.logger,
            sport_key="ncaaw_lacrosse",
        )

        # Check display modes to determine what data to fetch.
        # Keys match the adapter output in manager.py::_adapt_config_for_manager
        # and the plain "live"/"recent"/"upcoming" names used in
        # config_schema.json.
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("recent", False)
        self.upcoming_enabled = display_modes.get("upcoming", False)
        self.live_enabled = display_modes.get("live", False)
        self.league = "womens-college-lacrosse"

        self.logger.info(
            f"Initialized NCAAWLacrosse manager with display dimensions: {self.display_width}x{self.display_height}"
        )
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(
            f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}"
        )

    def _fetch_ncaa_lacrosse_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """Fetch the women's NCAA lacrosse season schedule (February through May)."""
        return self._fetch_season_schedule(
            sport="ncaa_womens_lacrosse",
            cache_key_prefix="ncaa_womens_lacrosse_schedule",
            scoreboard_url=ESPN_NCAAWLAX_SCOREBOARD_URL,
            season_start_mmdd="0201",
            use_cache=use_cache,
        )

    def _fetch_data(self) -> Optional[Dict]:
        """Default fetch: pull the cached season schedule.

        Live managers override this to query only today's games.
        """
        return self._fetch_ncaa_lacrosse_api_data(use_cache=True)


class NCAAWLacrosseLiveManager(BaseNCAAWLacrosseManager, LacrosseLive):
    """Manager for live NCAA Women's Lacrosse games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("NCAAWLacrosseLiveManager")

        # Initialize with test game only if test mode is enabled
        if self.test_mode:
            self.current_game = {
                "id": "401712999",
                "home_abbr": "BC",
                "away_abbr": "NU",
                "home_score": "11",
                "away_score": "9",
                "period": 3,
                "period_text": "Q3",
                "home_id": "103",
                "away_id": "111",
                "clock": "06:15",
                "home_logo_path": Path(self.logo_dir, "BC.png"),
                "away_logo_path": Path(self.logo_dir, "NU.png"),
                "game_time": "6:00 PM",
                "game_date": "Apr 13",
                "is_live": True,
                "is_final": False,
                "is_upcoming": False,
            }
            self.live_games = [self.current_game]
            self.logger.info(
                "Initialized NCAAWLacrosseLiveManager with test game: BC vs NU"
            )
        else:
            self.logger.info("Initialized NCAAWLacrosseLiveManager in live mode")

    def _fetch_data(self) -> Optional[Dict]:
        """Live fetch: pull today's games directly rather than the full season."""
        return self._fetch_todays_games()


class NCAAWLacrosseRecentManager(BaseNCAAWLacrosseManager, SportsRecent):
    """Manager for recently completed NCAA Women's Lacrosse games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("NCAAWLacrosseRecentManager")
        self.logger.info(
            f"Initialized NCAAWLacrosseRecentManager with {len(self.favorite_teams)} favorite teams"
        )


class NCAAWLacrosseUpcomingManager(BaseNCAAWLacrosseManager, SportsUpcoming):
    """Manager for upcoming NCAA Women's Lacrosse games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
    ):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("NCAAWLacrosseUpcomingManager")
        self.logger.info(
            f"Initialized NCAAWLacrosseUpcomingManager with {len(self.favorite_teams)} favorite teams"
        )
