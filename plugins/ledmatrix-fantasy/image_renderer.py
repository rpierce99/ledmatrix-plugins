"""
Image Renderer for ESPN Fantasy Sports Plugin

Creates PIL images for matchup scoreboard, standings ticker,
and roster breakdown displays. All layouts scale dynamically
based on display dimensions.
"""

import logging
import os
from typing import Dict, Any, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert a hex color string to an RGB tuple."""
    hex_color = hex_color.lstrip('#')
    try:
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (255, 255, 255)


class ImageRenderer:
    """Renders fantasy sports data as PIL images for the LED matrix."""

    # Font size presets keyed by display height ranges
    FONT_PROFILES = {
        # (small_size, medium_size, large_size)
        16: (5, 6, 8),
        32: (6, 8, 10),
        64: (8, 12, 16),
    }

    def __init__(self, display_width: int, display_height: int,
                 colors: Optional[Dict[str, str]] = None,
                 layout: Optional[Dict[str, Any]] = None,
                 logger: Optional[logging.Logger] = None):
        self.display_width = display_width
        self.display_height = display_height
        self.logger = logger or logging.getLogger(__name__)

        # Parse color config
        colors = colors or {}
        self.color_winning = _hex_to_rgb(colors.get('winning_color', '#00FF00'))
        self.color_losing = _hex_to_rgb(colors.get('losing_color', '#FF0000'))
        self.color_tied = _hex_to_rgb(colors.get('tied_color', '#FFFFFF'))
        self.color_my_team = _hex_to_rgb(colors.get('my_team_color', '#FFD700'))
        self.color_projection = _hex_to_rgb(colors.get('projection_color', '#888888'))
        self.color_header = _hex_to_rgb(colors.get('header_color', '#4488FF'))
        self.color_white = (255, 255, 255)
        self.color_dim = (100, 100, 100)
        self.color_separator = (60, 60, 60)

        # Layout config
        layout = layout or {}
        self.show_projections = layout.get('show_projections', True)
        self.show_header = layout.get('show_header', True)
        self.matchup_style = layout.get('matchup_style', 'split')

        # Load fonts scaled to display
        self.fonts = self._load_fonts()

    def _get_font_sizes(self) -> Tuple[int, int, int]:
        """Select font sizes based on display height."""
        h = self.display_height
        if h <= 16:
            return self.FONT_PROFILES[16]
        elif h <= 32:
            return self.FONT_PROFILES[32]
        return self.FONT_PROFILES[64]

    def _load_fonts(self) -> Dict[str, ImageFont.FreeTypeFont]:
        """Load fonts scaled to the display, with fallbacks."""
        fonts = {}
        font_dir = "assets/fonts"
        sm, md, lg = self._get_font_sizes()

        for name, size, preferred in [
            ('small', sm, "4x6-font.ttf"),
            ('medium', md, "PressStart2P-Regular.ttf"),
            ('large', lg, "PressStart2P-Regular.ttf"),
        ]:
            loaded = False
            for font_file in [preferred, "PressStart2P-Regular.ttf", "4x6-font.ttf"]:
                try:
                    fonts[name] = ImageFont.truetype(os.path.join(font_dir, font_file), size)
                    loaded = True
                    break
                except (IOError, OSError):
                    continue
            if not loaded:
                fonts[name] = ImageFont.load_default()

        return fonts

    def _text_width(self, text: str, font) -> int:
        """Get the pixel width of text."""
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]

    def _text_height(self, text: str, font) -> int:
        """Get the pixel height of text."""
        bbox = font.getbbox(text)
        return bbox[3] - bbox[1]

    def _draw_outlined_text(self, draw: ImageDraw.Draw, pos: Tuple[int, int],
                            text: str, font, fill: Tuple[int, int, int],
                            outline: Tuple[int, int, int] = (0, 0, 0)):
        """Draw text with a 1px outline for readability on LED matrix."""
        x, y = pos
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
        draw.text((x, y), text, font=font, fill=fill)

    def _center_x(self, text: str, font) -> int:
        """Get x position to center text horizontally."""
        return max(0, (self.display_width - self._text_width(text, font)) // 2)

    def _right_align_x(self, text: str, font, margin: int = 1) -> int:
        """Get x position to right-align text."""
        return max(0, self.display_width - self._text_width(text, font) - margin)

    def _get_score_color(self, my_score: float, opp_score: float) -> Tuple[int, int, int]:
        """Get color based on score comparison."""
        if my_score > opp_score:
            return self.color_winning
        elif my_score < opp_score:
            return self.color_losing
        return self.color_tied

    # ── Matchup Scoreboard ──────────────────────────────────────────

    def render_matchup(self, matchup_data: Dict[str, Any]) -> Optional[Image.Image]:
        """
        Render the matchup scoreboard as a fixed-size image.

        Split layout (scales to any display height):
          ┌──────────────────────────┐
          │       WEEK 7             │  <- header row (small font, dim)
          │ Mahomes Boys       98.4  │  <- my team + score (colored)
          │ Kelce Chiefs       87.2  │  <- opp team + score (inverse color)
          │     proj 113-106        │  <- projections (small, dim)
          └──────────────────────────┘
        """
        if not matchup_data:
            return None

        img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        font_sm = self.fonts['small']
        font_md = self.fonts['medium']

        my_score = matchup_data.get('my_score', 0)
        opp_score = matchup_data.get('opp_score', 0)
        my_projected = matchup_data.get('my_projected', 0)
        opp_projected = matchup_data.get('opp_projected', 0)
        week = matchup_data.get('week', '?')
        my_team = matchup_data.get('my_team', 'MY TEAM')
        opp_team = matchup_data.get('opp_team', 'OPPONENT')

        my_color = self._get_score_color(my_score, opp_score)
        opp_color = self._get_score_color(opp_score, my_score)

        # Calculate row heights dynamically
        sm_h = self._text_height("A", font_sm)
        md_h = self._text_height("A", font_md)

        # Vertical layout: header + my_team + opp_team + projections
        rows = []
        if self.show_header:
            rows.append(('header', sm_h))
        rows.append(('my_team', md_h))
        rows.append(('opp_team', md_h))
        if self.show_projections and my_projected and opp_projected:
            rows.append(('proj', sm_h))

        total_content_h = sum(h for _, h in rows)
        spacing = max(1, (self.display_height - total_content_h) // (len(rows) + 1))
        y = spacing

        for row_type, row_h in rows:
            if row_type == 'header':
                header_text = f"WEEK {week}"
                x = self._center_x(header_text, font_sm)
                self._draw_outlined_text(draw, (x, y), header_text, font_sm, self.color_header)

            elif row_type == 'my_team':
                self._draw_matchup_row(draw, y, my_team, my_score, my_color, font_md, font_sm)

            elif row_type == 'opp_team':
                self._draw_matchup_row(draw, y, opp_team, opp_score, opp_color, font_md, font_sm)

            elif row_type == 'proj':
                proj_text = f"proj {my_projected:.0f}-{opp_projected:.0f}"
                x = self._center_x(proj_text, font_sm)
                draw.text((x, y), proj_text, fill=self.color_projection, font=font_sm)

            y += row_h + spacing

        return img

    def _draw_matchup_row(self, draw: ImageDraw.Draw, y: int,
                          team_name: str, score: float,
                          color: Tuple[int, int, int],
                          name_font, score_font) -> None:
        """Draw a single matchup row: team name left-aligned, score right-aligned."""
        margin = 2
        score_text = f"{score:.1f}"
        score_w = self._text_width(score_text, name_font)

        # Truncate team name to leave room for score
        available = self.display_width - score_w - margin * 3
        truncated = team_name
        while self._text_width(truncated, score_font) > available and len(truncated) > 3:
            truncated = truncated[:-1]
        if truncated != team_name:
            truncated = truncated.rstrip() + ".."

        # Draw name (left) and score (right) on same row
        self._draw_outlined_text(draw, (margin, y), truncated, score_font, color)
        score_x = self.display_width - score_w - margin
        self._draw_outlined_text(draw, (score_x, y), score_text, name_font, color)

    # ── Standings Ticker ────────────────────────────────────────────

    def render_standings(self, standings_data: List[Dict[str, Any]]) -> Optional[Image.Image]:
        """
        Render standings as a two-row horizontal scrolling image.

        Top row:  rank + team name
        Bottom row: record (W-L) aligned under the name

        Returns a wide image to be scrolled by ScrollHelper.
        """
        if not standings_data:
            return None

        font_top = self.fonts['medium']
        font_bot = self.fonts['small']
        top_h = self._text_height("A", font_top)
        bot_h = self._text_height("0", font_bot)

        # Vertical positioning: two rows centered
        total_text_h = top_h + 2 + bot_h
        y_top = max(0, (self.display_height - total_text_h) // 2)
        y_bot = y_top + top_h + 2

        entry_gap = 12  # pixels between entries

        # First pass: measure widths
        entries = []
        for team in standings_data:
            rank = team.get('rank', '?')
            name = team.get('team_name', '???')
            wins = team.get('wins', 0)
            losses = team.get('losses', 0)
            ties = team.get('ties', 0)

            top_text = f"#{rank} {name}"
            record = f"{wins}-{losses}-{ties}" if ties > 0 else f"{wins}-{losses}"
            bot_text = f"({record})"

            top_w = self._text_width(top_text, font_top)
            bot_w = self._text_width(bot_text, font_bot)
            width = max(top_w, bot_w)
            is_mine = team.get('is_my_team', False)

            entries.append({
                'top_text': top_text, 'bot_text': bot_text,
                'top_w': top_w, 'bot_w': bot_w, 'width': width,
                'is_mine': is_mine,
            })

        total_width = sum(e['width'] + entry_gap for e in entries)
        total_width = max(total_width, self.display_width + 1)

        img = Image.new('RGB', (total_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        x = 0
        for entry in entries:
            color = self.color_my_team if entry['is_mine'] else self.color_white
            dim_color = self.color_my_team if entry['is_mine'] else self.color_dim

            self._draw_outlined_text(draw, (x, y_top), entry['top_text'], font_top, color)
            draw.text((x, y_bot), entry['bot_text'], fill=dim_color, font=font_bot)
            x += entry['width'] + entry_gap

            # Separator dot between entries
            if x < total_width - entry_gap:
                dot_y = self.display_height // 2 - 1
                draw.rectangle([x - entry_gap // 2 - 1, dot_y, x - entry_gap // 2 + 1, dot_y + 2],
                               fill=self.color_separator)

        return img

    # ── Roster Ticker ───────────────────────────────────────────────

    def render_roster(self, roster_data: List[Dict[str, Any]]) -> Optional[Image.Image]:
        """
        Render roster as a two-row horizontal scrolling image.

        Top row:  POS PlayerName
        Bottom row: 24.3 pts (projected: 22.1)

        Color: green if beating projection, red if under, dim if zero.
        Returns a wide image to be scrolled by ScrollHelper.
        """
        if not roster_data:
            return None

        font_top = self.fonts['medium']
        font_bot = self.fonts['small']
        top_h = self._text_height("A", font_top)
        bot_h = self._text_height("0", font_bot)

        total_text_h = top_h + 2 + bot_h
        y_top = max(0, (self.display_height - total_text_h) // 2)
        y_bot = y_top + top_h + 2

        entry_gap = 14

        starters = [p for p in roster_data if p.get('is_starter', True)]
        bench = [p for p in roster_data if not p.get('is_starter', True)]

        entries = []

        for player in starters:
            entries.append(self._build_roster_entry(player, font_top, font_bot, is_bench=False))

        if bench:
            # Bench header
            bench_top = "BENCH"
            bench_bot = ""
            bench_w = self._text_width(bench_top, font_top)
            entries.append({
                'top_text': bench_top, 'bot_text': bench_bot,
                'top_w': bench_w, 'bot_w': 0, 'width': bench_w,
                'color': self.color_separator, 'dim_color': self.color_separator,
            })
            for player in bench:
                entries.append(self._build_roster_entry(player, font_top, font_bot, is_bench=True))

        total_width = sum(e['width'] + entry_gap for e in entries)
        total_width = max(total_width, self.display_width + 1)

        img = Image.new('RGB', (total_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        x = 0
        for entry in entries:
            color = entry.get('color', self.color_white)
            dim_color = entry.get('dim_color', self.color_dim)

            self._draw_outlined_text(draw, (x, y_top), entry['top_text'], font_top, color)
            if entry['bot_text']:
                draw.text((x, y_bot), entry['bot_text'], fill=dim_color, font=font_bot)
            x += entry['width'] + entry_gap

        return img

    def _build_roster_entry(self, player: Dict[str, Any], font_top, font_bot,
                            is_bench: bool) -> Dict[str, Any]:
        """Build a roster entry dict with text, widths, and colors."""
        pos = player.get('position', '??')
        name = player.get('player_name', '???')
        points = player.get('points', 0)
        projected = player.get('projected', 0)

        top_text = f"{pos} {name}"
        if self.show_projections:
            bot_text = f"{points:.1f}pts (proj {projected:.0f})"
        else:
            bot_text = f"{points:.1f} pts"

        top_w = self._text_width(top_text, font_top)
        bot_w = self._text_width(bot_text, font_bot)

        if is_bench:
            color = self.color_dim
            dim_color = self.color_separator
        elif points == 0:
            color = self.color_dim
            dim_color = self.color_separator
        elif points >= projected:
            color = self.color_winning
            dim_color = self.color_projection
        else:
            color = self.color_losing
            dim_color = self.color_projection

        return {
            'top_text': top_text, 'bot_text': bot_text,
            'top_w': top_w, 'bot_w': bot_w,
            'width': max(top_w, bot_w),
            'color': color, 'dim_color': dim_color,
        }

    # ── Fallback ────────────────────────────────────────────────────

    def render_no_data(self) -> Image.Image:
        """Render a fallback message when no data is available."""
        img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        font_md = self.fonts['medium']
        font_sm = self.fonts['small']

        line1 = "FANTASY"
        line2 = "NO DATA"
        h1 = self._text_height(line1, font_md)
        h2 = self._text_height(line2, font_sm)
        gap = 3
        total = h1 + gap + h2
        y = (self.display_height - total) // 2

        x1 = self._center_x(line1, font_md)
        x2 = self._center_x(line2, font_sm)
        self._draw_outlined_text(draw, (x1, y), line1, font_md, self.color_header)
        draw.text((x2, y + h1 + gap), line2, fill=self.color_dim, font=font_sm)

        return img
