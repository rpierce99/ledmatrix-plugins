"""
ESPN Fantasy Sports Plugin for LEDMatrix

Displays ESPN Fantasy league matchup scores, standings, and roster
performance for football and basketball leagues.

Features:
- Live matchup scoreboard with color-coded scores
- League standings ticker with team highlighting
- Player-by-player roster breakdown
- Smart polling during game windows
- Multi-league rotation
- Mock data mode for development

API Version: 1.0.0
"""

import time
import logging
from typing import Dict, Any, List, Optional

from PIL import Image

from src.plugin_system.base_plugin import BasePlugin
from src.common.scroll_helper import ScrollHelper

from data_fetcher import DataFetcher
from image_renderer import ImageRenderer
from game_schedule import GameScheduler

logger = logging.getLogger(__name__)


class FantasyPlugin(BasePlugin):
    """
    ESPN Fantasy Sports plugin.

    Cycles through matchup, standings, and roster displays
    for one or more configured ESPN Fantasy leagues.
    """

    def __init__(self, plugin_id: str, config: Dict[str, Any],
                 display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        self.display_width = display_manager.width
        self.display_height = display_manager.height

        # Configuration
        self.display_duration = config.get('display_duration', 30)
        self.update_interval = config.get('update_interval', 300)
        self.scroll_speed = config.get('scroll_speed', 1.0)
        self.leagues_config = config.get('leagues', [])

        # Display mode toggles
        display_modes = config.get('display_modes', {})
        self.enabled_modes = []
        if display_modes.get('show_matchup', True):
            self.enabled_modes.append('matchup')
        if display_modes.get('show_standings', True):
            self.enabled_modes.append('standings')
        if display_modes.get('show_roster', False):
            self.enabled_modes.append('roster')
        if not self.enabled_modes:
            self.enabled_modes.append('matchup')

        # Matchup hold time
        self.matchup_hold = config.get('matchup_hold', 8)

        # Smart polling config
        polling_config = config.get('smart_polling', {})
        self.smart_polling_enabled = polling_config.get('enabled', True)
        self.live_interval = polling_config.get('live_interval', 60)
        self.idle_interval = polling_config.get('idle_interval', 3600)

        # Initialize components
        colors = config.get('customization', {})
        layout = config.get('layout', {})
        self.data_fetcher = DataFetcher(config, cache_manager, self.logger)
        self.renderer = ImageRenderer(self.display_width, self.display_height, colors, layout, self.logger)
        self.scheduler = GameScheduler(self.logger)
        self.scroll_helper = ScrollHelper(self.display_width, self.display_height, self.logger)

        # Configure scroll helper
        self.scroll_helper.set_scroll_speed(self.scroll_speed)
        self.scroll_helper.set_scroll_delay(0.01)

        # Display state
        self.current_mode_index = 0
        self.current_league_index = 0
        self.last_update = 0
        self.matchup_start_time = 0
        self._cycle_complete = False

        # Enable scrolling for ticker modes
        self.enable_scrolling = True

        self.logger.info(f"Fantasy plugin initialized: {len(self.leagues_config)} league(s), "
                        f"modes={self.enabled_modes}, "
                        f"display={self.display_width}x{self.display_height}")

        # Initial data fetch
        if self.leagues_config:
            self.update(force=True)
        else:
            self.logger.warning("No leagues configured — plugin will show no data until leagues are added")

    def update(self, force: bool = False) -> None:
        """Fetch data from ESPN for all configured leagues."""
        current_time = time.time()

        if not force:
            # Determine polling interval based on game schedule
            if self.smart_polling_enabled:
                sports = list(set(lc.get('sport', 'football') for lc in self.leagues_config))
                interval = self.scheduler.get_poll_interval(
                    sports, self.live_interval, self.idle_interval
                )
            else:
                interval = self.update_interval

            if current_time - self.last_update < interval:
                return

        self.logger.info(f"Updating fantasy data (force={force})")

        for league_cfg in self.leagues_config:
            self.data_fetcher.fetch_league_data(league_cfg)

        self.last_update = current_time

        # Clear scroll cache on data refresh
        self.scroll_helper.clear_cache()

    def display(self, force_clear: bool = False) -> None:
        """Render the current display mode."""
        if not self.enabled:
            return

        # Check if we have any data
        matchups = self.data_fetcher.get_matchup_data()
        standings = self.data_fetcher.get_standings_data()
        rosters = self.data_fetcher.get_roster_data()

        has_data = matchups or standings or rosters
        if not has_data:
            self.update(force=True)
            matchups = self.data_fetcher.get_matchup_data()
            standings = self.data_fetcher.get_standings_data()
            rosters = self.data_fetcher.get_roster_data()
            has_data = matchups or standings or rosters

        if not has_data:
            self._display_fallback()
            return

        current_mode = self.enabled_modes[self.current_mode_index]

        if current_mode == 'matchup':
            self._display_matchup(matchups, force_clear)
        elif current_mode == 'standings':
            self._display_standings(standings, force_clear)
        elif current_mode == 'roster':
            self._display_roster(rosters, force_clear)

    def _display_matchup(self, matchups: List[Dict[str, Any]], force_clear: bool) -> None:
        """Display the matchup scoreboard (fixed image with configurable hold time)."""
        if not matchups:
            self._advance_mode()
            return

        now = time.time()

        # Track how long we've been showing this matchup
        if self.matchup_start_time == 0 or force_clear:
            self.matchup_start_time = now

        # Auto-advance after hold time
        if now - self.matchup_start_time >= self.matchup_hold:
            self.matchup_start_time = 0
            self._advance_mode()
            return

        # Rotate through leagues
        idx = self.current_league_index % len(matchups)
        matchup = matchups[idx]

        img = self.renderer.render_matchup(matchup)
        if img:
            self.display_manager.set_scrolling_state(False)
            self.display_manager.image.paste(img, (0, 0))
            self.display_manager.update_display()

    def _display_standings(self, standings_list: List[Dict[str, Any]], force_clear: bool) -> None:
        """Display standings as a scrolling ticker."""
        if not standings_list:
            self._advance_mode()
            return

        idx = self.current_league_index % len(standings_list)
        standings = standings_list[idx].get('standings', [])

        if not self.scroll_helper.cached_image or force_clear:
            img = self.renderer.render_standings(standings)
            if img:
                self.scroll_helper.set_image(img)
                self.scroll_helper.reset_scroll()

        if self.scroll_helper.cached_image:
            self.display_manager.set_scrolling_state(True)
            visible = self.scroll_helper.get_visible_frame()
            if visible:
                self.display_manager.image.paste(visible, (0, 0))
                self.display_manager.update_display()

            if self.scroll_helper.is_scroll_complete():
                self._cycle_complete = True

    def _display_roster(self, rosters: List[Dict[str, Any]], force_clear: bool) -> None:
        """Display roster breakdown as a scrolling ticker."""
        if not rosters:
            self._advance_mode()
            return

        idx = self.current_league_index % len(rosters)
        roster = rosters[idx].get('roster', [])

        if not self.scroll_helper.cached_image or force_clear:
            img = self.renderer.render_roster(roster)
            if img:
                self.scroll_helper.set_image(img)
                self.scroll_helper.reset_scroll()

        if self.scroll_helper.cached_image:
            self.display_manager.set_scrolling_state(True)
            visible = self.scroll_helper.get_visible_frame()
            if visible:
                self.display_manager.image.paste(visible, (0, 0))
                self.display_manager.update_display()

            if self.scroll_helper.is_scroll_complete():
                self._cycle_complete = True

    def _display_fallback(self) -> None:
        """Show a fallback message when no data is available."""
        img = self.renderer.render_no_data()
        self.display_manager.image.paste(img, (0, 0))
        self.display_manager.update_display()

    def _advance_mode(self) -> None:
        """Move to the next display mode, cycling through leagues."""
        self.current_mode_index = (self.current_mode_index + 1) % len(self.enabled_modes)
        if self.current_mode_index == 0:
            self.current_league_index += 1
        self.scroll_helper.clear_cache()
        self._cycle_complete = False

    def is_cycle_complete(self) -> bool:
        """Check if all modes/leagues have been displayed once."""
        return self._cycle_complete

    def reset_cycle_state(self) -> None:
        """Reset the display cycle."""
        self._cycle_complete = False
        self.current_mode_index = 0
        self.current_league_index = 0
        self.scroll_helper.clear_cache()

    def get_display_duration(self) -> float:
        """Return configured display duration."""
        return self.display_duration
