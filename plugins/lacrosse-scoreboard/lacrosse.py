import logging
from typing import Any, Dict, Optional

from PIL import Image, ImageDraw, ImageFont

from data_sources import ESPNDataSource
from sports import SportsCore, SportsLive


class Lacrosse(SportsCore):
    """Base class for lacrosse sports with common functionality."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.data_source = ESPNDataSource(logger)
        self.sport = "lacrosse"
        self.show_shots = self.mode_config.get("show_shots", False)

    def _extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract relevant game details from ESPN Lacrosse API response."""
        details, home_team, away_team, status, situation = (
            self._extract_game_details_common(game_event)
        )
        if details is None or home_team is None or away_team is None or status is None:
            return
        try:
            competition = game_event["competitions"][0]
            status = competition["status"]

            # Lacrosse shot totals (if exposed in statistics)
            home_stats = home_team.get("statistics", [])
            away_stats = away_team.get("statistics", [])
            home_shots = next(
                (
                    int(c["displayValue"])
                    for c in home_stats
                    if c.get("name") in ("shots", "totalShots")
                ),
                0,
            )
            away_shots = next(
                (
                    int(c["displayValue"])
                    for c in away_stats
                    if c.get("name") in ("shots", "totalShots")
                ),
                0,
            )

            # Format period/quarter. NCAA men's & women's lacrosse use 4 quarters.
            period = status.get("period", 0)
            period_text = ""
            if status["type"]["state"] == "in":
                if period == 0:
                    period_text = "Start"
                elif 1 <= period <= 4:
                    period_text = f"Q{period}"
                elif period > 4:
                    period_text = f"OT{period - 4}"
            elif status["type"]["state"] == "post":
                if period > 4:
                    period_text = "Final/OT"
                else:
                    period_text = "Final"
            elif status["type"]["state"] == "pre":
                period_text = details.get("game_time", "")

            details.update(
                {
                    "period": period,
                    "period_text": period_text,
                    "clock": status.get("displayClock", "0:00"),
                    "home_shots": home_shots,
                    "away_shots": away_shots,
                }
            )

            if not details["home_abbr"] or not details["away_abbr"]:
                self.logger.warning(
                    f"Missing team abbreviation in event: {details['id']}"
                )
                return None

            self.logger.debug(
                f"Extracted: {details['away_abbr']}@{details['home_abbr']}, "
                f"Status: {status['type']['name']}, Live: {details['is_live']}, "
                f"Final: {details['is_final']}, Upcoming: {details['is_upcoming']}"
            )

            return details
        except Exception as e:
            self.logger.error(
                f"Error extracting game details: {e} from event: {game_event.get('id')}",
                exc_info=True,
            )
            return None


class LacrosseLive(Lacrosse, SportsLive):
    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)

    def _test_mode_update(self):
        if self.current_game and self.current_game["is_live"]:
            # For testing, tick the clock down to show updates working.
            minutes = int(self.current_game["clock"].split(":")[0])
            seconds = int(self.current_game["clock"].split(":")[1])
            seconds -= 1
            if seconds < 0:
                seconds = 59
                minutes -= 1
                if minutes < 0:
                    minutes = 14  # 15-minute quarters (NCAA men's)
                    if self.current_game["period"] < 4:
                        self.current_game["period"] += 1
                    else:
                        self.current_game["period"] = 1
            self.current_game["clock"] = f"{minutes:02d}:{seconds:02d}"

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the detailed scorebug layout for a live Lacrosse game."""
        try:
            main_img = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 255)
            )
            overlay = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 0)
            )
            draw_overlay = ImageDraw.Draw(overlay)
            home_logo = self._load_and_resize_logo(
                game["home_id"],
                game["home_abbr"],
                game["home_logo_path"],
                game.get("home_logo_url"),
            )
            away_logo = self._load_and_resize_logo(
                game["away_id"],
                game["away_abbr"],
                game["away_logo_path"],
                game.get("away_logo_url"),
            )

            if not home_logo or not away_logo:
                self.logger.error(
                    f"Failed to load logos for live game: {game.get('id')}"
                )
                draw_final = ImageDraw.Draw(main_img.convert("RGB"))
                self._draw_text_with_outline(
                    draw_final, "Logo Error", (5, 5), self.fonts["status"]
                )
                self.display_manager.image.paste(main_img.convert("RGB"), (0, 0))
                self.display_manager.update_display()
                return

            center_y = self.display_height // 2

            home_x = (
                self.display_width - home_logo.width + 10
                + self._get_layout_offset('home_logo', 'x_offset')
            )
            home_y = center_y - (home_logo.height // 2) + self._get_layout_offset('home_logo', 'y_offset')
            main_img.paste(home_logo, (home_x, home_y), home_logo)

            away_x = -10 + self._get_layout_offset('away_logo', 'x_offset')
            away_y = center_y - (away_logo.height // 2) + self._get_layout_offset('away_logo', 'y_offset')
            main_img.paste(away_logo, (away_x, away_y), away_logo)

            # Quarter and Clock (Top center)
            period_clock_text = (
                f"{game.get('period_text', '')} {game.get('clock', '')}".strip()
            )
            if game.get("is_period_break"):
                period_clock_text = game.get("status_text", "Quarter Break")

            status_width = draw_overlay.textlength(
                period_clock_text, font=self.fonts["time"]
            )
            status_x = (self.display_width - status_width) // 2 + self._get_layout_offset('status_text', 'x_offset')
            status_y = 1 + self._get_layout_offset('status_text', 'y_offset')
            self._draw_text_with_outline(
                draw_overlay,
                period_clock_text,
                (status_x, status_y),
                self.fonts["time"],
            )

            # Scores (centered, slightly above bottom)
            home_score = str(game.get("home_score", "0"))
            away_score = str(game.get("away_score", "0"))
            score_text = f"{away_score}-{home_score}"
            score_width = draw_overlay.textlength(score_text, font=self.fonts["score"])
            score_x = (self.display_width - score_width) // 2 + self._get_layout_offset('score', 'x_offset')
            score_y = (
                self.display_height // 2
            ) - 3 + self._get_layout_offset('score', 'y_offset')
            self._draw_text_with_outline(
                draw_overlay, score_text, (score_x, score_y), self.fonts["score"]
            )

            # Shot totals
            if self.show_shots:
                shots_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                home_shots = str(game.get("home_shots", "0"))
                away_shots = str(game.get("away_shots", "0"))
                shots_text = f"{away_shots}   SHOTS   {home_shots}"
                shots_bbox = draw_overlay.textbbox((0, 0), shots_text, font=shots_font)
                shots_height = shots_bbox[3] - shots_bbox[1]
                shots_y = self.display_height - shots_height - 1
                shots_width = draw_overlay.textlength(shots_text, font=shots_font)
                shots_x = (self.display_width - shots_width) // 2
                self._draw_text_with_outline(
                    draw_overlay, shots_text, (shots_x, shots_y), shots_font
                )

            # Draw odds if available
            if "odds" in game and game["odds"]:
                self._draw_dynamic_odds(
                    draw_overlay, game["odds"], self.display_width, self.display_height
                )

            # Draw records or rankings if enabled
            if self.show_records or self.show_ranking:
                try:
                    record_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                except IOError:
                    record_font = ImageFont.load_default()

                away_abbr = game.get("away_abbr", "")
                home_abbr = game.get("home_abbr", "")

                record_bbox = draw_overlay.textbbox((0, 0), "0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height - 1

                if away_abbr:
                    if self.show_ranking and self.show_records:
                        away_rank = self._team_rankings_cache.get(away_abbr, 0)
                        away_text = f"#{away_rank}" if away_rank > 0 else ""
                    elif self.show_ranking:
                        away_rank = self._team_rankings_cache.get(away_abbr, 0)
                        away_text = f"#{away_rank}" if away_rank > 0 else ""
                    elif self.show_records:
                        away_text = game.get("away_record", "")
                    else:
                        away_text = ""

                    if away_text:
                        self._draw_text_with_outline(
                            draw_overlay,
                            away_text,
                            (3, record_y),
                            record_font,
                        )

                if home_abbr:
                    if self.show_ranking and self.show_records:
                        home_rank = self._team_rankings_cache.get(home_abbr, 0)
                        home_text = f"#{home_rank}" if home_rank > 0 else ""
                    elif self.show_ranking:
                        home_rank = self._team_rankings_cache.get(home_abbr, 0)
                        home_text = f"#{home_rank}" if home_rank > 0 else ""
                    elif self.show_records:
                        home_text = game.get("home_record", "")
                    else:
                        home_text = ""

                    if home_text:
                        home_record_bbox = draw_overlay.textbbox(
                            (0, 0), home_text, font=record_font
                        )
                        home_record_width = home_record_bbox[2] - home_record_bbox[0]
                        home_record_x = self.display_width - home_record_width - 3
                        self._draw_text_with_outline(
                            draw_overlay,
                            home_text,
                            (home_record_x, record_y),
                            record_font,
                        )

            # Composite text overlay onto main image
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert("RGB")

            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display()

        except Exception as e:
            self.logger.error(
                f"Error displaying live Lacrosse game: {e}", exc_info=True
            )
