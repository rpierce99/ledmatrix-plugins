"""
Space & Astronomy Tracker Plugin for LEDMatrix

Displays ISS pass alerts, rocket launch countdowns, visible planets
with pixel-art icons, constellation line art, and NASA APOD — all
over a twinkling starfield background.

Features:
- ISS pass countdown with "OVERHEAD NOW" pulsing alert
- Next rocket launch countdown with mission details
- Visible planets with dithered icons and constellations
- People in space scrolling ticker
- NASA APOD title ticker (optional, needs API key)
- Twinkling starfield background on all screens
- Zero API keys needed for core features

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

logger = logging.getLogger(__name__)


class SpacePlugin(BasePlugin):
    """Space & Astronomy Tracker plugin."""

    def __init__(self, plugin_id: str, config: Dict[str, Any],
                 display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        self.display_width = display_manager.width
        self.display_height = display_manager.height

        # Config
        self.display_duration = config.get('display_duration', 30)
        self.mode_hold = config.get('mode_hold', 10)
        self.update_interval = config.get('update_interval', 600)
        self.scroll_speed = config.get('scroll_speed', 1.0)

        # Display modes
        modes_cfg = config.get('display_modes', {})
        self.enabled_modes = []
        if modes_cfg.get('show_iss', True):
            self.enabled_modes.append('iss')
        if modes_cfg.get('show_launches', True):
            self.enabled_modes.append('launch')
        if modes_cfg.get('show_night_sky', True):
            self.enabled_modes.append('night_sky')
        if modes_cfg.get('show_apod', False):
            self.enabled_modes.append('apod')
        if not self.enabled_modes:
            self.enabled_modes.append('iss')

        # Components
        colors = config.get('customization', {})
        starfield = config.get('starfield', {})
        self.data_fetcher = DataFetcher(config, cache_manager, self.logger)
        self.renderer = ImageRenderer(
            self.display_width, self.display_height,
            colors, starfield, self.logger
        )
        self.scroll_helper = ScrollHelper(self.display_width, self.display_height, self.logger)
        self.scroll_helper.set_scroll_speed(self.scroll_speed)
        self.scroll_helper.set_scroll_delay(0.01)
        if hasattr(self.scroll_helper, 'set_target_fps'):
            self.scroll_helper.set_target_fps(100)

        # State
        self.current_mode_index = 0
        self.mode_start_time = 0
        self.last_update = 0
        self._cycle_complete = False
        self.enable_scrolling = True

        self.logger.info(f"Space plugin initialized: modes={self.enabled_modes}, "
                        f"display={self.display_width}x{self.display_height}")

        # Initial data fetch
        self.update(force=True)

    def update(self, force: bool = False) -> None:
        """Fetch data from all sources."""
        now = time.time()
        if not force and now - self.last_update < self.update_interval:
            return

        self.logger.info(f"Updating space data (force={force})")

        modes_cfg = self.config.get('display_modes', {})

        if modes_cfg.get('show_iss', True):
            self.data_fetcher.fetch_iss_data(force)

        if modes_cfg.get('show_launches', True):
            self.data_fetcher.fetch_launch_data(force)

        if modes_cfg.get('show_night_sky', True):
            self.data_fetcher.fetch_night_sky_data(force)

        if modes_cfg.get('show_apod', False):
            self.data_fetcher.fetch_apod_data(force)

        self.last_update = now

    def display(self, force_clear: bool = False) -> None:
        """Render the current display mode."""
        if not self.enabled:
            return

        # ISS overhead alert takes priority over everything
        iss_data = self.data_fetcher.get_iss_data()
        iss_cfg = self.config.get('iss', {})
        if iss_cfg.get('alert_enabled', True) and iss_data.get('is_overhead'):
            self._display_iss_alert(iss_data)
            return

        current_mode = self.enabled_modes[self.current_mode_index]

        if current_mode == 'iss':
            self._display_iss(iss_data, force_clear)
        elif current_mode == 'launch':
            self._display_launch(force_clear)
        elif current_mode == 'night_sky':
            self._display_night_sky(force_clear)
        elif current_mode == 'apod':
            self._display_apod(force_clear)

    def _display_iss(self, iss_data: Dict[str, Any], force_clear: bool) -> None:
        """Display ISS countdown (static) then people in space (scrolling)."""
        now = time.time()

        if self.mode_start_time == 0 or force_clear:
            self.mode_start_time = now
            self.scroll_helper.clear_cache()

        elapsed = now - self.mode_start_time

        if elapsed < self.mode_hold:
            # Static countdown
            img = self.renderer.render_iss(iss_data)
            self.display_manager.set_scrolling_state(False)
            self.display_manager.image.paste(img, (0, 0))
            self.display_manager.update_display()
        else:
            # Scrolling people in space
            people = iss_data.get('people_in_space', [])
            if people:
                if not self.scroll_helper.cached_image:
                    ticker = self.renderer.render_people_in_space(people)
                    self.scroll_helper.set_image(ticker)
                    self.scroll_helper.reset_scroll()

                self.display_manager.set_scrolling_state(True)
                visible = self.scroll_helper.get_visible_frame()
                if visible:
                    self.display_manager.image.paste(visible, (0, 0))
                    self.display_manager.update_display()

                if self.scroll_helper.is_scroll_complete():
                    self._advance_mode()
            else:
                self._advance_mode()

    def _display_iss_alert(self, iss_data: Dict[str, Any]) -> None:
        """Display ISS overhead alert (takes priority)."""
        img = self.renderer.render_iss(iss_data)
        self.display_manager.set_scrolling_state(False)
        self.display_manager.image.paste(img, (0, 0))
        self.display_manager.update_display()

    def _display_launch(self, force_clear: bool) -> None:
        """Display launch countdown (static) then mission ticker (scrolling)."""
        now = time.time()
        launch_data = self.data_fetcher.get_launch_data()

        if self.mode_start_time == 0 or force_clear:
            self.mode_start_time = now
            self.scroll_helper.clear_cache()

        elapsed = now - self.mode_start_time

        if elapsed < self.mode_hold:
            # Static countdown
            img = self.renderer.render_launch(launch_data)
            self.display_manager.set_scrolling_state(False)
            self.display_manager.image.paste(img, (0, 0))
            self.display_manager.update_display()
        else:
            # Scrolling mission details
            if not self.scroll_helper.cached_image:
                ticker = self.renderer.render_launch_ticker(launch_data)
                self.scroll_helper.set_image(ticker)
                self.scroll_helper.reset_scroll()

            self.display_manager.set_scrolling_state(True)
            visible = self.scroll_helper.get_visible_frame()
            if visible:
                self.display_manager.image.paste(visible, (0, 0))
                self.display_manager.update_display()

            if self.scroll_helper.is_scroll_complete():
                self._advance_mode()

    def _display_night_sky(self, force_clear: bool) -> None:
        """Display planets and constellation (static with hold timer)."""
        now = time.time()

        if self.mode_start_time == 0 or force_clear:
            self.mode_start_time = now

        if now - self.mode_start_time >= self.mode_hold:
            self._advance_mode()
            return

        planets = self.data_fetcher.get_planets_data()
        constellation = self.data_fetcher.get_constellation_data()

        img = self.renderer.render_night_sky(planets, constellation)
        self.display_manager.set_scrolling_state(False)
        self.display_manager.image.paste(img, (0, 0))
        self.display_manager.update_display()

    def _display_apod(self, force_clear: bool) -> None:
        """Display APOD title as scrolling ticker."""
        apod_data = self.data_fetcher.get_apod_data()

        if not self.scroll_helper.cached_image or force_clear:
            img = self.renderer.render_apod(apod_data)
            self.scroll_helper.set_image(img)
            self.scroll_helper.reset_scroll()

        self.display_manager.set_scrolling_state(True)
        visible = self.scroll_helper.get_visible_frame()
        if visible:
            self.display_manager.image.paste(visible, (0, 0))
            self.display_manager.update_display()

        if self.scroll_helper.is_scroll_complete():
            self._advance_mode()

    def _advance_mode(self) -> None:
        """Move to the next display mode."""
        self.current_mode_index += 1
        if self.current_mode_index >= len(self.enabled_modes):
            self.current_mode_index = 0
            self._cycle_complete = True

        self.scroll_helper.clear_cache()
        self.mode_start_time = 0

    def is_cycle_complete(self) -> bool:
        return self._cycle_complete

    def reset_cycle_state(self) -> None:
        self._cycle_complete = False
        self.current_mode_index = 0
        self.scroll_helper.clear_cache()
        self.mode_start_time = 0

    def get_display_duration(self) -> float:
        return self.display_duration
