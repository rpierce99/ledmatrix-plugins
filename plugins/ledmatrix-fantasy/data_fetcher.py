"""
Data Fetcher for ESPN Fantasy Sports Plugin

Handles ESPN Fantasy API communication using the espn-api library,
with caching and mock data support for development.
"""

import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime


class MockDataProvider:
    """Provides realistic mock data for development and testing."""

    FOOTBALL_MATCHUP = {
        'league_name': 'Office League 2025',
        'week': 7,
        'my_team': 'Mahomes Boys',
        'my_score': 98.4,
        'my_projected': 112.5,
        'opp_team': 'Kelce Chiefs',
        'opp_score': 87.2,
        'opp_projected': 105.8,
        'is_live': True,
    }

    BASKETBALL_MATCHUP = {
        'league_name': 'Hoops Dynasty',
        'week': 14,
        'my_team': 'Curry Shooters',
        'my_score': 456.3,
        'my_projected': 520.0,
        'opp_team': 'Jokic MVPs',
        'opp_score': 441.7,
        'opp_projected': 498.2,
        'is_live': True,
    }

    FOOTBALL_STANDINGS = [
        {'team_name': 'Mahomes Boys', 'wins': 5, 'losses': 1, 'ties': 0, 'rank': 1, 'points_for': 782.3, 'is_my_team': True},
        {'team_name': 'Kelce Chiefs', 'wins': 4, 'losses': 2, 'ties': 0, 'rank': 2, 'points_for': 721.1, 'is_my_team': False},
        {'team_name': 'Hurts So Good', 'wins': 4, 'losses': 2, 'ties': 0, 'rank': 3, 'points_for': 698.4, 'is_my_team': False},
        {'team_name': 'Allen Ivrsn', 'wins': 3, 'losses': 3, 'ties': 0, 'rank': 4, 'points_for': 667.9, 'is_my_team': False},
        {'team_name': 'Lamar Express', 'wins': 3, 'losses': 3, 'ties': 0, 'rank': 5, 'points_for': 654.2, 'is_my_team': False},
        {'team_name': 'Hill Toppers', 'wins': 2, 'losses': 4, 'ties': 0, 'rank': 6, 'points_for': 612.8, 'is_my_team': False},
        {'team_name': 'CMC Runners', 'wins': 2, 'losses': 4, 'ties': 0, 'rank': 7, 'points_for': 588.5, 'is_my_team': False},
        {'team_name': 'Last Place', 'wins': 1, 'losses': 5, 'ties': 0, 'rank': 8, 'points_for': 501.2, 'is_my_team': False},
    ]

    BASKETBALL_STANDINGS = [
        {'team_name': 'Curry Shooters', 'wins': 10, 'losses': 3, 'ties': 0, 'rank': 1, 'points_for': 6234.1, 'is_my_team': True},
        {'team_name': 'Jokic MVPs', 'wins': 9, 'losses': 4, 'ties': 0, 'rank': 2, 'points_for': 6102.3, 'is_my_team': False},
        {'team_name': 'Giannis Gang', 'wins': 8, 'losses': 5, 'ties': 0, 'rank': 3, 'points_for': 5890.7, 'is_my_team': False},
        {'team_name': 'Luka Magic', 'wins': 7, 'losses': 6, 'ties': 0, 'rank': 4, 'points_for': 5678.4, 'is_my_team': False},
        {'team_name': 'Ant Attack', 'wins': 6, 'losses': 7, 'ties': 0, 'rank': 5, 'points_for': 5432.1, 'is_my_team': False},
        {'team_name': 'Tatum Time', 'wins': 4, 'losses': 9, 'ties': 0, 'rank': 6, 'points_for': 5123.8, 'is_my_team': False},
    ]

    FOOTBALL_ROSTER = [
        {'player_name': 'P. Mahomes', 'position': 'QB', 'points': 24.3, 'projected': 22.1, 'is_starter': True},
        {'player_name': 'D. Henry', 'position': 'RB', 'points': 18.7, 'projected': 14.2, 'is_starter': True},
        {'player_name': 'B. Robinson', 'position': 'RB', 'points': 12.1, 'projected': 11.8, 'is_starter': True},
        {'player_name': 'J. Chase', 'position': 'WR', 'points': 22.4, 'projected': 16.5, 'is_starter': True},
        {'player_name': 'A. Brown', 'position': 'WR', 'points': 8.9, 'projected': 13.1, 'is_starter': True},
        {'player_name': 'T. Kelce', 'position': 'TE', 'points': 11.2, 'projected': 10.4, 'is_starter': True},
        {'player_name': 'J. Tucker', 'position': 'K', 'points': 8.0, 'projected': 7.5, 'is_starter': True},
        {'player_name': 'Bills D/ST', 'position': 'D/ST', 'points': 6.0, 'projected': 6.8, 'is_starter': True},
        {'player_name': 'K. Williams', 'position': 'RB', 'points': 4.2, 'projected': 9.3, 'is_starter': False},
        {'player_name': 'G. Wilson', 'position': 'WR', 'points': 0.0, 'projected': 11.2, 'is_starter': False},
    ]

    BASKETBALL_ROSTER = [
        {'player_name': 'S. Curry', 'position': 'PG', 'points': 48.2, 'projected': 44.5, 'is_starter': True},
        {'player_name': 'D. Booker', 'position': 'SG', 'points': 38.1, 'projected': 36.0, 'is_starter': True},
        {'player_name': 'J. Tatum', 'position': 'SF', 'points': 42.7, 'projected': 40.2, 'is_starter': True},
        {'player_name': 'G. Antetokounmpo', 'position': 'PF', 'points': 55.3, 'projected': 48.1, 'is_starter': True},
        {'player_name': 'N. Jokic', 'position': 'C', 'points': 61.2, 'projected': 52.0, 'is_starter': True},
        {'player_name': 'T. Haliburton', 'position': 'PG', 'points': 0.0, 'projected': 35.4, 'is_starter': False},
        {'player_name': 'P. Banchero', 'position': 'PF', 'points': 0.0, 'projected': 32.1, 'is_starter': False},
    ]

    @classmethod
    def get_matchup(cls, sport: str) -> Dict[str, Any]:
        if sport == 'basketball':
            return cls.BASKETBALL_MATCHUP.copy()
        return cls.FOOTBALL_MATCHUP.copy()

    @classmethod
    def get_standings(cls, sport: str) -> List[Dict[str, Any]]:
        if sport == 'basketball':
            return [s.copy() for s in cls.BASKETBALL_STANDINGS]
        return [s.copy() for s in cls.FOOTBALL_STANDINGS]

    @classmethod
    def get_roster(cls, sport: str) -> List[Dict[str, Any]]:
        if sport == 'basketball':
            return [r.copy() for r in cls.BASKETBALL_ROSTER]
        return [r.copy() for r in cls.FOOTBALL_ROSTER]


class DataFetcher:
    """Fetches and caches ESPN Fantasy league data."""

    def __init__(self, config: Dict[str, Any], cache_manager, logger: Optional[logging.Logger] = None):
        self.config = config
        self.cache_manager = cache_manager
        self.logger = logger or logging.getLogger(__name__)
        self.espn_s2 = config.get('espn_s2', '')
        self.swid = config.get('swid', '')
        self._leagues: Dict[str, Any] = {}
        self._league_data: Dict[str, Dict[str, Any]] = {}
        self._has_credentials = bool(self.espn_s2 and self.swid)
        self._has_leagues = bool(config.get('leagues'))
        # Use mock data only when no leagues are configured at all
        self._use_mock = not self._has_leagues

        if self._use_mock:
            self.logger.info("No leagues configured — using mock data for preview")
        elif not self._has_credentials:
            self.logger.info("No ESPN credentials — will attempt public league access")
        else:
            self.logger.info("ESPN credentials found — will connect to live API")

    def _get_espn_league(self, league_cfg: Dict[str, Any]):
        """Get or create an espn-api League instance for a league config."""
        sport = league_cfg['sport']
        league_id = league_cfg['league_id']
        year = league_cfg.get('year', datetime.now().year)
        cache_key = f"{sport}_{league_id}_{year}"

        if cache_key in self._leagues:
            return self._leagues[cache_key]

        try:
            if sport == 'football':
                from espn_api.football import League
            elif sport == 'basketball':
                from espn_api.basketball import League
            else:
                self.logger.error(f"Unsupported sport: {sport}")
                return None

            # Public leagues work without credentials
            kwargs = {'league_id': league_id, 'year': year}
            if self._has_credentials:
                kwargs['espn_s2'] = self.espn_s2
                kwargs['swid'] = self.swid

            league = League(**kwargs)
            self._leagues[cache_key] = league
            self.logger.info(f"Connected to ESPN {sport} league {league_id} ({year})")
            return league
        except Exception as e:
            self.logger.error(f"Failed to connect to ESPN league {league_id}: {e}")
            return None

    def fetch_league_data(self, league_cfg: Dict[str, Any]) -> None:
        """Fetch all data for a league and cache it."""
        sport = league_cfg['sport']
        league_id = league_cfg['league_id']
        data_key = f"{sport}_{league_id}"

        if self._use_mock:
            self._league_data[data_key] = {
                'sport': sport,
                'matchup': MockDataProvider.get_matchup(sport),
                'standings': MockDataProvider.get_standings(sport),
                'roster': MockDataProvider.get_roster(sport),
                'team_name_override': league_cfg.get('team_name'),
                'last_fetch': time.time(),
            }
            self.logger.debug(f"Loaded mock data for {data_key}")
            return

        # Try to load from cache first
        cache_key = f"fantasy_{data_key}"
        cached = self.cache_manager.get_cached_data_with_strategy(cache_key, 'fantasy') if hasattr(self.cache_manager, 'get_cached_data_with_strategy') else None

        league = self._get_espn_league(league_cfg)
        if not league:
            # Fall back to cached data or mock
            if cached:
                self._league_data[data_key] = cached
                self.logger.info(f"Using cached data for {data_key} (API unavailable)")
            else:
                self.logger.warning(f"No API connection and no cache for {data_key} — falling back to mock data")
                self._league_data[data_key] = {
                    'sport': sport,
                    'matchup': MockDataProvider.get_matchup(sport),
                    'standings': MockDataProvider.get_standings(sport),
                    'roster': MockDataProvider.get_roster(sport),
                    'team_name_override': league_cfg.get('team_name'),
                    'last_fetch': time.time(),
                }
            return

        try:
            matchup = self._fetch_matchup(league, sport)
            standings = self._fetch_standings(league, sport)
            roster = self._fetch_roster(league, sport)

            self._league_data[data_key] = {
                'sport': sport,
                'matchup': matchup,
                'standings': standings,
                'roster': roster,
                'team_name_override': league_cfg.get('team_name'),
                'last_fetch': time.time(),
            }

            # Cache the data
            self.cache_manager.set(cache_key, self._league_data[data_key])
            self.logger.info(f"Fetched and cached data for {data_key}")
        except Exception as e:
            self.logger.error(f"Error fetching league data for {data_key}: {e}", exc_info=True)
            # Fall back to cached data
            if cached:
                self._league_data[data_key] = cached
                self.logger.info(f"Using cached data for {data_key} after fetch error")

    def _find_my_team(self, league):
        """Find the user's team in the league."""
        for team in league.teams:
            if hasattr(team, 'owners') and team.owners:
                for owner in team.owners:
                    owner_id = owner.get('id', '') if isinstance(owner, dict) else str(owner)
                    if owner_id == self.swid or owner_id == self.swid.strip('{}'):
                        return team
        # Fallback: return first team if we can't match
        if league.teams:
            self.logger.warning("Could not match SWID to a team owner, using first team")
            return league.teams[0]
        return None

    def _fetch_matchup(self, league, sport: str) -> Optional[Dict[str, Any]]:
        """Fetch current matchup data."""
        try:
            my_team = self._find_my_team(league)
            if not my_team:
                self.logger.warning("Could not find user's team in league")
                return None

            # Get current week's matchup
            current_week = league.current_week if hasattr(league, 'current_week') else 1
            matchups = league.box_scores(current_week)

            for matchup in matchups:
                home_team = matchup.home_team
                away_team = matchup.away_team

                if home_team == my_team:
                    return {
                        'league_name': league.settings.name if hasattr(league, 'settings') else 'Fantasy League',
                        'week': current_week,
                        'my_team': my_team.team_name,
                        'my_score': matchup.home_score,
                        'my_projected': getattr(matchup, 'home_projected', matchup.home_score),
                        'opp_team': away_team.team_name if away_team else 'BYE',
                        'opp_score': matchup.away_score if away_team else 0,
                        'opp_projected': getattr(matchup, 'away_projected', matchup.away_score) if away_team else 0,
                        'is_live': True,
                    }
                elif away_team == my_team:
                    return {
                        'league_name': league.settings.name if hasattr(league, 'settings') else 'Fantasy League',
                        'week': current_week,
                        'my_team': my_team.team_name,
                        'my_score': matchup.away_score,
                        'my_projected': getattr(matchup, 'away_projected', matchup.away_score),
                        'opp_team': home_team.team_name,
                        'opp_score': matchup.home_score,
                        'opp_projected': getattr(matchup, 'home_projected', matchup.home_score),
                        'is_live': True,
                    }

            self.logger.warning(f"No matchup found for team in week {current_week}")
            return None
        except Exception as e:
            self.logger.error(f"Error fetching matchup: {e}", exc_info=True)
            return None

    def _fetch_standings(self, league, sport: str) -> List[Dict[str, Any]]:
        """Fetch league standings."""
        try:
            my_team = self._find_my_team(league)
            standings = []

            sorted_teams = sorted(league.teams, key=lambda t: t.standing if hasattr(t, 'standing') else 0)

            for i, team in enumerate(sorted_teams):
                standings.append({
                    'team_name': team.team_name,
                    'wins': team.wins,
                    'losses': team.losses,
                    'ties': getattr(team, 'ties', 0),
                    'rank': team.standing if hasattr(team, 'standing') else i + 1,
                    'points_for': getattr(team, 'points_for', 0),
                    'is_my_team': team == my_team,
                })

            return standings
        except Exception as e:
            self.logger.error(f"Error fetching standings: {e}", exc_info=True)
            return []

    def _fetch_roster(self, league, sport: str) -> List[Dict[str, Any]]:
        """Fetch roster data for user's team."""
        try:
            my_team = self._find_my_team(league)
            if not my_team:
                return []

            roster = []
            current_week = league.current_week if hasattr(league, 'current_week') else 1
            box_scores = league.box_scores(current_week)

            # Find my box score to get player stats
            my_lineup = None
            for box in box_scores:
                if box.home_team == my_team:
                    my_lineup = box.home_lineup
                    break
                elif box.away_team == my_team:
                    my_lineup = box.away_lineup
                    break

            if my_lineup:
                for player in my_lineup:
                    roster.append({
                        'player_name': self._abbreviate_name(player.name),
                        'position': player.position if hasattr(player, 'position') else '??',
                        'points': player.points if hasattr(player, 'points') else 0,
                        'projected': player.projected_points if hasattr(player, 'projected_points') else 0,
                        'is_starter': player.slot_position != 'BE' if hasattr(player, 'slot_position') else True,
                    })
            elif hasattr(my_team, 'roster'):
                for player in my_team.roster:
                    roster.append({
                        'player_name': self._abbreviate_name(player.name),
                        'position': player.position if hasattr(player, 'position') else '??',
                        'points': getattr(player, 'points', 0),
                        'projected': getattr(player, 'projected_points', 0),
                        'is_starter': True,
                    })

            # Sort: starters first, then by points descending
            roster.sort(key=lambda p: (not p['is_starter'], -p['points']))
            return roster
        except Exception as e:
            self.logger.error(f"Error fetching roster: {e}", exc_info=True)
            return []

    @staticmethod
    def _abbreviate_name(full_name: str) -> str:
        """Abbreviate a player name: 'Patrick Mahomes' -> 'P. Mahomes'."""
        parts = full_name.split()
        if len(parts) >= 2:
            return f"{parts[0][0]}. {parts[-1]}"
        return full_name

    def get_all_league_data(self) -> Dict[str, Dict[str, Any]]:
        """Return all cached league data."""
        return self._league_data

    def get_matchup_data(self, sport: str = None, league_id: int = None) -> List[Dict[str, Any]]:
        """Get matchup data for all leagues or a specific one."""
        results = []
        for key, data in self._league_data.items():
            if sport and not key.startswith(sport):
                continue
            matchup = data.get('matchup')
            if matchup:
                if data.get('team_name_override'):
                    matchup = matchup.copy()
                    matchup['my_team'] = data['team_name_override']
                results.append(matchup)
        return results

    def get_standings_data(self, sport: str = None) -> List[Dict[str, Any]]:
        """Get standings data as list of (league_info, standings) tuples."""
        results = []
        for key, data in self._league_data.items():
            if sport and not key.startswith(sport):
                continue
            standings = data.get('standings', [])
            if standings:
                results.append({
                    'sport': data['sport'],
                    'league_key': key,
                    'standings': standings,
                })
        return results

    def get_roster_data(self, sport: str = None) -> List[Dict[str, Any]]:
        """Get roster data for all leagues."""
        results = []
        for key, data in self._league_data.items():
            if sport and not key.startswith(sport):
                continue
            roster = data.get('roster', [])
            if roster:
                results.append({
                    'sport': data['sport'],
                    'league_key': key,
                    'roster': roster,
                })
        return results
