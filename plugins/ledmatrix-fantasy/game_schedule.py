"""
Smart Game Scheduler for ESPN Fantasy Sports Plugin

Determines optimal polling intervals based on sport-specific game windows.
Reduces unnecessary API calls during off-hours and off-season.
"""

import logging
from datetime import datetime, time as dtime
from typing import Optional


# NFL game windows (local time)
NFL_WINDOWS = [
    # (day_of_week, start_hour, start_min, end_hour, end_min)
    (3, 19, 0, 23, 45),   # Thursday 7:00 PM - 11:45 PM
    (6, 12, 0, 23, 59),   # Sunday 12:00 PM - midnight
    (0, 19, 0, 23, 45),   # Monday 7:00 PM - 11:45 PM
    (5, 20, 0, 23, 45),   # Saturday (playoffs) 8:00 PM - 11:45 PM
]

# NFL season months (September through early February)
NFL_SEASON_MONTHS = {9, 10, 11, 12, 1}

# NBA season months (October through June)
NBA_SEASON_MONTHS = {10, 11, 12, 1, 2, 3, 4, 5, 6}

# NBA games happen most evenings
NBA_GAME_HOURS = (18, 23)  # 6 PM - 11 PM typical


class GameScheduler:
    """Determines when to poll ESPN based on sport-specific game schedules."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)

    def is_in_season(self, sport: str, now: Optional[datetime] = None) -> bool:
        """Check if the given sport is currently in season."""
        now = now or datetime.now()
        month = now.month

        if sport == 'football':
            return month in NFL_SEASON_MONTHS
        elif sport == 'basketball':
            return month in NBA_SEASON_MONTHS
        return True

    def is_game_window(self, sport: str, now: Optional[datetime] = None) -> bool:
        """Check if we're currently in an active game window for the sport."""
        now = now or datetime.now()

        if not self.is_in_season(sport, now):
            return False

        if sport == 'football':
            return self._is_nfl_game_window(now)
        elif sport == 'basketball':
            return self._is_nba_game_window(now)
        return True

    def _is_nfl_game_window(self, now: datetime) -> bool:
        """Check if we're in an NFL game window."""
        weekday = now.weekday()  # 0=Monday
        for day, sh, sm, eh, em in NFL_WINDOWS:
            if weekday == day:
                start = dtime(sh, sm)
                end = dtime(eh, em)
                current = now.time()
                if start <= current <= end:
                    return True
        return False

    def _is_nba_game_window(self, now: datetime) -> bool:
        """NBA games happen most evenings during season."""
        if not self.is_in_season('basketball', now):
            return False
        hour = now.hour
        return NBA_GAME_HOURS[0] <= hour <= NBA_GAME_HOURS[1]

    def get_poll_interval(self, sports: list, live_interval: int = 60,
                          idle_interval: int = 3600) -> int:
        """
        Get the recommended polling interval based on current game state.

        Args:
            sports: List of sport strings to check (e.g., ['football', 'basketball'])
            live_interval: Interval during active game windows (seconds)
            idle_interval: Interval outside game windows (seconds)

        Returns:
            Recommended polling interval in seconds
        """
        now = datetime.now()

        # If any sport has an active game window, use live interval
        for sport in sports:
            if self.is_game_window(sport, now):
                self.logger.debug(f"{sport} game window active — using live interval ({live_interval}s)")
                return live_interval

        # Check if any sport is in season
        any_in_season = any(self.is_in_season(s, now) for s in sports)
        if any_in_season:
            # In season but not game time — use moderate interval
            moderate = min(idle_interval, 1800)  # Cap at 30 min during season
            self.logger.debug(f"In season but no game window — using moderate interval ({moderate}s)")
            return moderate

        # Off season entirely
        self.logger.debug(f"Off season — using idle interval ({idle_interval}s)")
        return idle_interval
