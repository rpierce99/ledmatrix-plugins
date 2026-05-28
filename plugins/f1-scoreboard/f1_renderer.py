"""
F1 Renderer Module

Renders all F1 display mode cards as PIL Images for the LED matrix.
All layouts are fully dynamic - dimensions are proportional to display size.
Supports 64x32, 128x32, 96x48, 192x48, and any other matrix configuration.
"""

import logging
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pytz
from PIL import Image, ImageDraw, ImageFont

from circuit_data import get_circuit_info
from logo_downloader import F1LogoLoader
from team_colors import (F1_RED, PODIUM_COLORS, get_team_color)

logger = logging.getLogger(__name__)

ACCENT_BAR_RATIO = 0.025  # ~3px on 128-wide display

# Short display names that fit in 4x6-font at 6px without truncation
_TEAM_SHORT = {
    "mclaren":      "McLaren",
    "red_bull":     "Red Bull",
    "ferrari":      "Ferrari",
    "mercedes":     "Mercedes",
    "aston_martin": "Aston M.",   # "Aston Mtn" is 5px too wide for PressStart2P
    "alpine":       "Alpine",
    "haas":         "Haas",
    "sauber":       "Sauber",
    "williams":     "Williams",
    "rb":           "RB F1",
    "cadillac":     "Cadillac",
}


def _team_short(constructor_id: str) -> str:
    return _TEAM_SHORT.get(constructor_id, constructor_id.replace("_", " ").title())


def _team_color_bright(constructor_id: str, min_max: int = 150) -> tuple:
    """Return team color, boosted to ensure minimum readability on dark backgrounds."""
    color = get_team_color(constructor_id)
    peak = max(color)
    if peak < min_max:
        scale = min_max / peak
        color = tuple(min(255, int(c * scale)) for c in color)
    return color


class F1Renderer:
    """Renders F1 display cards as PIL Images."""

    def __init__(self, display_width: int, display_height: int,
                 config: Optional[Dict[str, Any]] = None,
                 logo_loader: Optional[F1LogoLoader] = None,
                 custom_logger: Optional[logging.Logger] = None):
        self.display_width = display_width
        self.display_height = display_height
        self.config = config or {}
        self.logger = custom_logger or logger
        self.logo_loader = logo_loader or F1LogoLoader()
        self.accent_bar_width = max(2, int(display_width * ACCENT_BAR_RATIO))

        # Logo sizes: up to 85% of content height
        self.logo_max = int(display_height * 0.85)

        # Fixed right-side stat zone (points + W/P column)
        # ~30% of display width, minimum 34px, maximum 48px
        self.stat_zone_w = max(34, min(48, int(display_width * 0.30)))

        # ── Visual feature flags (from config["visual"]) ──────────────
        vis = self.config.get("visual", {})

        fl = vis.get("fastest_lap_dot", {})
        self.show_fl_dot = fl.get("enabled", True)
        raw_color = fl.get("color", [180, 0, 255])
        self.fl_dot_color = tuple(raw_color) if isinstance(raw_color, (list, tuple)) else (180, 0, 255)

        gb = vis.get("gap_bar", {})
        self.show_gap_bar = gb.get("enabled", True)

        hdr = vis.get("standings_header", {})
        self.show_standings_header = hdr.get("enabled", True)
        self.standings_header_show_round = hdr.get("show_round", True)

        cm = vis.get("circuit_map", {})
        self.show_circuit_map = cm.get("enabled", True)

        cl = vis.get("championship_leaders", {})
        self.show_championship_leaders = cl.get("enabled", True)

        cb = vis.get("championship_battle", {})
        self.show_championship_battle = cb.get("enabled", True)

        ctb = vis.get("constructor_battle", {})
        self.show_constructor_battle = ctb.get("enabled", True)

        df = vis.get("driver_form", {})
        self.show_driver_form = df.get("enabled", True)

        rr = self.config.get("recent_races", {})
        self.show_position_delta = rr.get("show_position_delta", True)
        self.show_dnf_status = rr.get("show_dnf_status", True)

        cs = self.config.get("constructor_standings", {})
        self.show_driver_split = cs.get("show_driver_split", True)

        up = self.config.get("upcoming", {})
        self.show_circuit_info = up.get("show_circuit_info", True)

        self.fonts = self._load_fonts()

    def _load_fonts(self) -> Dict[str, Any]:
        height_scale = self.display_height / 32.0
        cfg = self.config.get("customization", {})

        fonts = {}
        # header / position: PressStart2P blocky pixel font
        for key, default_size in [("header", 8), ("position", 8)]:
            fonts[key] = self._load_font(
                cfg.get(key + "_text", {}).get("font", "PressStart2P-Regular.ttf"),
                int(cfg.get(key + "_text", {}).get("font_size", max(6, int(default_size * height_scale)))))
        # detail / small: compact 4x6 font
        for key, default_size in [("detail", 6), ("small", 6)]:
            fonts[key] = self._load_font(
                cfg.get(key + "_text", {}).get("font", "4x6-font.ttf"),
                int(cfg.get(key + "_text", {}).get("font_size", max(5, int(default_size * height_scale)))))

        return fonts

    def _load_font(self, font_name: str, size: int) -> Union[ImageFont.FreeTypeFont, Any]:
        paths = [
            str(Path(__file__).parent / "assets" / "fonts" / font_name),
            f"assets/fonts/{font_name}",
            str(Path(__file__).parent.parent.parent / "assets" / "fonts" / font_name),
        ]
        for path in paths:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        self.logger.warning("Could not load font %s size %d, using default", font_name, size)
        return ImageFont.load_default()

    def _to_local_dt(self, utc_iso_str: str) -> datetime:
        dt = datetime.fromisoformat(utc_iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.UTC)
        tz_str = self.config.get("timezone", "UTC")
        try:
            local_tz = pytz.timezone(tz_str)
        except pytz.exceptions.UnknownTimeZoneError:
            local_tz = pytz.UTC
        return dt.astimezone(local_tz)

    # ─── Text Helpers ──────────────────────────────────────────────────

    def _draw_text_outlined(self, draw: ImageDraw.ImageDraw, xy: Tuple[int, int],
                            text: str, font, fill=(255, 255, 255), outline=(0, 0, 0)):
        x, y = xy
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
        draw.text((x, y), text, font=font, fill=fill)

    def _tw(self, draw: ImageDraw.ImageDraw, text: str, font) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    def _th(self, draw: ImageDraw.ImageDraw, text: str, font) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]

    def _truncate(self, draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
        if self._tw(draw, text, font) <= max_w:
            return text
        while len(text) > 1:
            text = text[:-1]
            if self._tw(draw, text + "..", font) <= max_w:
                return text + ".."
        return text

    # ─── Accent Bar ───────────────────────────────────────────────────

    def _draw_accent_bar(self, draw: ImageDraw.ImageDraw, constructor_id: str,
                         x: int = 0, extra: int = 0):
        """Team-color vertical bar on the left edge."""
        color = get_team_color(constructor_id)
        w = self.accent_bar_width + extra
        draw.rectangle([x, 0, x + w - 1, self.display_height - 1], fill=color)

    # ─── Championship Gap Bar ─────────────────────────────────────────

    def _draw_gap_bar(self, draw: ImageDraw.ImageDraw, entry: Dict,
                      constructor_id: str, x_start: int = None, x_end: int = None):
        """Proportional points bar at bottom of card (2px tall)."""
        leader_pts = entry.get("leader_points", 0)
        pts = entry.get("points", 0)
        if leader_pts <= 0:
            return

        bar_h = max(2, self.display_height // 16)
        x_s = x_start if x_start is not None else self.accent_bar_width + 1
        x_e = x_end if x_end is not None else self.display_width - 1
        bar_w = x_e - x_s
        if bar_w <= 0:
            return

        y_top = self.display_height - bar_h
        # Background
        draw.rectangle([x_s, y_top, x_e, self.display_height - 1], fill=(25, 25, 25))
        # Filled
        fill_w = int(bar_w * min(1.0, pts / leader_pts))
        if fill_w > 0:
            color = get_team_color(constructor_id)
            dim = tuple(max(0, int(c * 0.7)) for c in color)
            draw.rectangle([x_s, y_top, x_s + fill_w - 1, self.display_height - 1], fill=dim)

    # ─── Live Badge ───────────────────────────────────────────────────

    def _draw_live_badge(self, draw: ImageDraw.ImageDraw, session_type: str = "RACE"):
        pulse = max(160, min(255, int(200 + 55 * math.sin(time.time() * 4))))
        abbr = {"Race": "RACE", "Qual": "QUALI", "FP1": "FP1", "FP2": "FP2",
                "FP3": "FP3", "SS": "S.Q", "SR": "SPR"}
        label = "● " + abbr.get(session_type, session_type)
        bw = self._tw(draw, label, self.fonts["small"]) + 4
        bh = self._th(draw, label, self.fonts["small"]) + 2
        bx = self.display_width - bw - 1
        by = 1
        draw.rectangle([bx, by, bx + bw, by + bh], fill=(80, 0, 0))
        draw.text((bx + 2, by + 1), label, font=self.fonts["small"], fill=(pulse, 60, 60))

    # ─── Standings Section Header ──────────────────────────────────────

    def render_standings_header(self, title: str, round_num: int = 0,
                                 total_rounds: int = 24,
                                 season: int = 2026) -> Image.Image:
        """
        Intro card shown before the driver or constructor standings scroll.
        Shows title + season + round progress bar.

        Layout:
          Row 1: [F1 logo] [TITLE] (e.g. "DRIVER STANDINGS")
          Row 2: [2026]  [Rd N of M]
          Row 3: [season progress bar]
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        # Dark background
        draw.rectangle([0, 0, self.display_width - 1, self.display_height - 1], fill=(8, 8, 8))

        # Red left accent
        draw.rectangle([0, 0, 2, self.display_height - 1], fill=F1_RED)

        x = 4
        y = 2

        # F1 logo
        logo_h = min(10, self.display_height // 3)
        f1_logo = self.logo_loader.get_f1_logo(max_height=logo_h, max_width=int(self.display_width * 0.12))
        if f1_logo:
            img.paste(f1_logo, (x, y), f1_logo)
            x += f1_logo.width + 3

        # Title (e.g. "DRIVER STANDINGS")
        title_trunc = self._truncate(draw, title, self.fonts["detail"], self.display_width - x - 2)
        self._draw_text_outlined(draw, (x, y), title_trunc, self.fonts["detail"], fill=(220, 220, 220))
        y += self._th(draw, "A", self.fonts["detail"]) + 2

        # Season year + round info
        if round_num > 0 and self.standings_header_show_round:
            year_text = str(season)
            rd_text = f"Rd {round_num}/{total_rounds}"
            self._draw_text_outlined(draw, (4, y), year_text, self.fonts["small"], fill=(100, 100, 100))
            rd_x = self.display_width - self._tw(draw, rd_text, self.fonts["small"]) - 3
            self._draw_text_outlined(draw, (rd_x, y), rd_text, self.fonts["small"], fill=(255, 180, 0))
            y += self._th(draw, "A", self.fonts["small"]) + 2

        # Season progress bar
        if round_num > 0 and total_rounds > 0 and y + 3 < self.display_height:
            bar_x0 = 4
            bar_x1 = self.display_width - 4
            bar_w = bar_x1 - bar_x0
            bar_y = y
            draw.rectangle([bar_x0, bar_y, bar_x1, bar_y + 2], fill=(30, 30, 30))
            fill_w = int(bar_w * min(1.0, round_num / total_rounds))
            if fill_w > 0:
                draw.rectangle([bar_x0, bar_y, bar_x0 + fill_w, bar_y + 2], fill=F1_RED)

        return img

    # ─── Driver Standings Card ─────────────────────────────────────────

    def render_driver_standing(self, entry: Dict,
                                is_live: bool = False,
                                live_session: str = "") -> Image.Image:
        """
        Layout (128×32):
          [acc][logo][ P# CODE         ][PTS  ]
                     [ team name       ][ W•P ]
          [═══════════════gap bar══════════════]
        Stat zone = rightmost stat_zone_w pixels. Content never enters it.
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        cid = entry.get("constructor_id", "")
        is_fav = entry.get("is_favorite", False)

        # Background tint for favorites
        if is_fav:
            tc = get_team_color(cid)
            tint = tuple(max(0, int(c * 0.15)) for c in tc)
            draw.rectangle([0, 0, self.display_width - 1, self.display_height - 1], fill=tint)

        # Gap bar at bottom
        gap_h = max(2, self.display_height // 16)
        content_h = self.display_height - gap_h
        if self.show_gap_bar:
            self._draw_gap_bar(draw, entry, cid)

        # Accent bar (wider for favorites)
        self._draw_accent_bar(draw, cid, extra=1 if is_fav else 0)
        x = self.accent_bar_width + (1 if is_fav else 0) + 2

        # Team logo
        logo = self.logo_loader.get_team_logo(cid, max(8, int(content_h * 0.85)), max(8, int(content_h * 0.85)))
        if logo:
            ly = (content_h - logo.height) // 2
            img.paste(logo, (x, ly), logo)
            x += logo.width + 2

        # Zone boundaries
        content_max_x = self.display_width - self.stat_zone_w
        stat_x = self.display_width - self.stat_zone_w + 1

        # ── Row 1: Position + Code ──────────────────────────────────
        pos_text = f"P{entry.get('position', '?')}"
        pos_color = (255, 215, 0) if is_fav else (200, 200, 200)
        self._draw_text_outlined(draw, (x, 2), pos_text, self.fonts["position"], fill=pos_color)
        px = x + self._tw(draw, pos_text, self.fonts["position"]) + 3

        code = entry.get("code", "???")
        code_color = (255, 215, 0) if is_fav else (255, 255, 255)
        code_text = self._truncate(draw, code, self.fonts["position"], content_max_x - px)
        self._draw_text_outlined(draw, (px, 2), code_text, self.fonts["position"], fill=code_color)

        # ── Row 2: Team name ──────────────────────────────────────
        row2_y = 2 + self._th(draw, pos_text, self.fonts["position"]) + 2
        if row2_y + 5 < content_h:
            team_disp = _team_short(cid)
            team_disp = self._truncate(draw, team_disp, self.fonts["small"], content_max_x - x)
            self._draw_text_outlined(draw, (x, row2_y), team_disp, self.fonts["small"],
                                     fill=_team_color_bright(cid), outline=(0, 0, 0))

        # ── Stat zone: Points (row 1) + Wins/Poles (row 2) ────────
        pts_text = f"{int(entry.get('points', 0))}pt"
        self._draw_text_outlined(draw, (stat_x, 2), pts_text, self.fonts["detail"], fill=(255, 220, 50))

        row2_stat_y = 2 + self._th(draw, pts_text, self.fonts["detail"]) + 2
        wins = entry.get("wins", 0)
        poles = entry.get("poles", 0)
        wp_text = f"{wins}W {poles}P"
        if row2_stat_y + 5 < content_h:
            self._draw_text_outlined(draw, (stat_x, row2_stat_y), wp_text,
                                     self.fonts["small"], fill=(160, 160, 160))

        if is_live and live_session:
            self._draw_live_badge(draw, live_session)

        return img

    # ─── Constructor Standings Card ────────────────────────────────────

    def render_constructor_standing(self, entry: Dict,
                                     is_live: bool = False,
                                     live_session: str = "") -> Image.Image:
        """
        Layout (128×32):
          [acc][logo][ TEAM NAME (big, team color)  ][PTS  ]
                     [ P# · wins                    ][ W   ]
          [═══════════════gap bar══════════════════════════]
        Team name occupies its own row for full width — no prefix competing for space.
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        cid = entry.get("constructor_id", "")
        is_fav = entry.get("is_favorite", False)
        tc = get_team_color(cid)

        if is_fav:
            tint = tuple(max(0, int(c * 0.15)) for c in tc)
            draw.rectangle([0, 0, self.display_width - 1, self.display_height - 1], fill=tint)

        gap_h = max(2, self.display_height // 16)
        content_h = self.display_height - gap_h
        if self.show_gap_bar:
            self._draw_gap_bar(draw, entry, cid)
        self._draw_accent_bar(draw, cid, extra=1 if is_fav else 0)
        x = self.accent_bar_width + (1 if is_fav else 0) + 2

        # Logo: height capped at 85% of content, width capped at 15px to leave room for name
        logo_h = max(8, int(content_h * 0.85))
        logo_w = min(15, logo_h)
        logo = self.logo_loader.get_team_logo(cid, logo_h, logo_w)
        if logo:
            ly = (content_h - logo.height) // 2
            img.paste(logo, (x, ly), logo)
            x += logo.width + 2

        # Fixed stat zone on the right
        stat_x = self.display_width - self.stat_zone_w + 1
        content_max_x = stat_x - 2

        # ── Row 1: TEAM NAME — use position font if it fits, else detail ──
        team_name = _team_short(cid)
        avail = content_max_x - x
        name_font = self.fonts["position"] if self._tw(draw, team_name, self.fonts["position"]) <= avail else self.fonts["detail"]
        team_name = self._truncate(draw, team_name, name_font, avail)
        name_y = 1 if name_font is self.fonts["position"] else 4
        self._draw_text_outlined(draw, (x, name_y), team_name, name_font,
                                 fill=_team_color_bright(cid), outline=(0, 0, 0))

        # ── Row 1 stat: Points (right-aligned) ───────────────────
        pts_text = f"{int(entry.get('points', 0))}pt"
        self._draw_text_outlined(draw, (stat_x, 2), pts_text, self.fonts["detail"],
                                 fill=(255, 220, 50))

        # ── Row 2: P# (left) + Wins (right stat) ─────────────────
        row2_y = name_y + self._th(draw, team_name, name_font) + 2
        if row2_y + 5 < content_h:
            pos_text = f"P{entry.get('position', '?')}"
            pos_color = (255, 215, 0) if is_fav else (150, 150, 150)
            self._draw_text_outlined(draw, (x, row2_y), pos_text, self.fonts["small"],
                                     fill=pos_color)

            wins = entry.get("wins", 0)
            wins_y = 2 + self._th(draw, pts_text, self.fonts["detail"]) + 2
            if wins_y + 4 < content_h:
                self._draw_text_outlined(draw, (stat_x, wins_y), f"{wins}W",
                                         self.fonts["small"], fill=(160, 160, 160))

            # ── Row 3: Driver points split ────────────────────────────
            if self.show_driver_split:
                team_drivers = entry.get("team_drivers", [])[:2]
                if team_drivers:
                    row3_y = row2_y + self._th(draw, pos_text, self.fonts["small"]) + 2
                    if row3_y + 4 < content_h:
                        tc_bright = _team_color_bright(cid, min_max=120)
                        parts = []
                        for d in team_drivers:
                            code = d.get("code", "???")
                            pts_d = int(d.get("points", 0))
                            parts.append((code, str(pts_d)))

                        # Left-align first driver, right-align second driver
                        if len(parts) >= 1:
                            d1_code, d1_pts = parts[0]
                            d1_str = f"{d1_code} {d1_pts}"
                            draw.text((x, row3_y), d1_str,
                                      font=self.fonts["small"], fill=tc_bright)
                        if len(parts) >= 2:
                            d2_code, d2_pts = parts[1]
                            d2_str = f"{d2_code} {d2_pts}"
                            d2_w = self._tw(draw, d2_str, self.fonts["small"])
                            d2_x = content_max_x - d2_w
                            d1_right = x + self._tw(draw, d1_str, self.fonts["small"]) + 2
                            if d2_x > d1_right:
                                dim_color = tuple(max(0, int(c * 0.80)) for c in tc_bright)
                                draw.text((d2_x, row3_y), d2_str,
                                          font=self.fonts["small"], fill=dim_color)

        if is_live and live_session:
            self._draw_live_badge(draw, live_session)

        return img

    # ─── Recent Race Results Card ──────────────────────────────────────

    def render_race_result(self, race: Dict) -> Image.Image:
        """
        Layout:
          [GP NAME                         DATE]
          [P1  CODE  TIME  |  P2  CODE  |  P3  CODE  ]
          [team bar        |  gap text   |  gap text  ]
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        results = race.get("results", [])
        race_name = race.get("race_name", "Grand Prix")
        short_name = race_name.replace("Grand Prix", "GP")

        # ── Header row ───────────────────────────────────────────────
        header_h = self._th(draw, "A", self.fonts["detail"]) + 1
        draw.rectangle([0, 0, self.display_width - 1, header_h + 1], fill=(20, 0, 0))

        short_name_trunc = self._truncate(draw, short_name, self.fonts["detail"],
                                          self.display_width - 40)
        self._draw_text_outlined(draw, (3, 1), short_name_trunc, self.fonts["detail"], fill=F1_RED)

        # Date right-aligned in header
        if race.get("date"):
            try:
                raw = race["date"]
                dt = self._to_local_dt(raw + "T12:00:00Z" if "T" not in raw else raw)
                date_str = dt.strftime("%b %d").upper()
                dw = self._tw(draw, date_str, self.fonts["small"])
                self._draw_text_outlined(draw, (self.display_width - dw - 2, 1),
                                         date_str, self.fonts["small"], fill=(110, 110, 110))
            except (ValueError, TypeError):
                pass

        # ── Podium section ────────────────────────────────────────────
        podium_y = header_h + 2
        content_h = self.display_height - podium_y
        top_n = min(len(results), 3)
        if top_n == 0:
            return img

        section_w = self.display_width // top_n
        divider_color = (40, 40, 40)

        for i in range(top_n):
            r = results[i]
            pos = r.get("position", i + 1)
            code = r.get("code", "???")
            cid = r.get("constructor_id", "")
            tc = get_team_color(cid)
            medal = PODIUM_COLORS.get(pos, (180, 180, 180))
            x0 = i * section_w
            x1 = x0 + section_w - 1

            # Subtle team color background in section
            tint = tuple(max(0, int(c * 0.08)) for c in tc)
            draw.rectangle([x0, podium_y, x1, self.display_height - 1], fill=tint)

            # Team color accent top bar (thin)
            draw.rectangle([x0, podium_y, x1, podium_y + 1], fill=tc)

            # Divider line
            if i > 0:
                draw.line([(x0, podium_y), (x0, self.display_height - 1)], fill=divider_color)

            # Position label (medal colored)
            pos_label = f"P{pos}"
            py2 = podium_y + 3
            self._draw_text_outlined(draw, (x0 + 2, py2), pos_label,
                                     self.fonts["small"], fill=medal)
            label_w = self._tw(draw, pos_label, self.fonts["small"])

            # Mini logo (top-right of section)
            mini_logo = self.logo_loader.get_team_logo(
                cid, max_height=int(content_h * 0.45), max_width=int(section_w * 0.35))
            logo_x = x1 - (mini_logo.width + 1) if mini_logo else x1
            logo_y_pos = podium_y + 2
            if mini_logo and logo_y_pos + mini_logo.height <= self.display_height:
                img.paste(mini_logo, (logo_x, logo_y_pos), mini_logo)

            # Position delta (+N gained / -N lost) — right of pos label, before logo
            grid_pos = r.get("grid", 0)
            if self.show_position_delta and grid_pos > 0:
                delta = grid_pos - pos
                if delta > 0:
                    delta_str = f"+{delta}"
                    delta_color = (0, 210, 80)
                elif delta < 0:
                    delta_str = str(delta)
                    delta_color = (220, 50, 50)
                else:
                    delta_str = ""
                if delta_str:
                    d_w = self._tw(draw, delta_str, self.fonts["small"])
                    available_right = (logo_x - 3) if mini_logo else (x1 - 2)
                    label_right = x0 + 2 + label_w + 2
                    d_x = available_right - d_w
                    if d_x > label_right:
                        draw.text((d_x, py2), delta_str,
                                  font=self.fonts["small"], fill=delta_color)

            # Driver code on second row
            code_y = py2 + self._th(draw, pos_label, self.fonts["small"]) + 1
            code_max_w = section_w - 4 - (mini_logo.width + 2 if mini_logo else 0)
            code_trunc = self._truncate(draw, code, self.fonts["detail"], code_max_w)
            self._draw_text_outlined(draw, (x0 + 2, code_y), code_trunc,
                                     self.fonts["detail"], fill=(240, 240, 240))

            # Gap/time or retirement status on third row
            status = r.get("status", "")
            gap_y = code_y + self._th(draw, "A", self.fonts["detail"]) + 1
            if gap_y + 5 < self.display_height:
                if self.show_dnf_status and status and status not in ("Finished", ""):
                    # Retired / lapped
                    if status.startswith("+") and "Lap" in status:
                        parts = status.split()
                        gap_str = f"+{parts[0].lstrip('+')}L"
                        gap_color = (180, 120, 40)
                    else:
                        gap_str = "RET"
                        gap_color = (180, 60, 60)
                    gap_trunc = self._truncate(draw, gap_str, self.fonts["small"], section_w - 4)
                    draw.text((x0 + 2, gap_y), gap_trunc,
                              font=self.fonts["small"], fill=gap_color)
                else:
                    if i == 0:
                        gap_str = r.get("time", "")
                        gap_color = (160, 160, 160)
                    else:
                        gap_str = r.get("time", r.get("gap", ""))
                        gap_color = (255, 190, 50)
                    if gap_str:
                        gap_trunc = self._truncate(draw, gap_str, self.fonts["small"], section_w - 4)
                        draw.text((x0 + 2, gap_y), gap_trunc,
                                  font=self.fonts["small"], fill=gap_color)

            # Fastest lap: 3×3 dot in top-right corner of section
            if self.show_fl_dot and r.get("fastest_lap", False):
                fl_x = x1 - 4
                fl_y = podium_y + 2
                draw.rectangle([fl_x, fl_y, fl_x + 2, fl_y + 2], fill=self.fl_dot_color)

            # Team color line at bottom
            draw.rectangle([x0 + 1, self.display_height - 2, x1 - 1, self.display_height - 1],
                           fill=tuple(max(0, int(c * 0.6)) for c in tc))

        return img

    # ─── Favorite Driver Race Highlight Card ───────────────────────────

    def render_favorite_race_card(self, race: Dict, result: Dict) -> Image.Image:
        """
        Full-width single-driver card shown when the favorite driver finishes
        outside the podium. Shows position, driver code, gap, delta, and points.
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        cid = result.get("constructor_id", "")
        pos = result.get("position", 0)
        code = result.get("code", "???")
        tc = get_team_color(cid)
        tc_bright = _team_color_bright(cid)

        # Subtle team background tint
        tint = tuple(max(0, int(c * 0.10)) for c in tc)
        draw.rectangle([0, 0, self.display_width - 1, self.display_height - 1], fill=tint)

        # ── Header (same style as podium card) ─────────────────────────
        header_h = self._th(draw, "A", self.fonts["detail"]) + 1
        draw.rectangle([0, 0, self.display_width - 1, header_h + 1], fill=(20, 0, 0))
        race_name = race.get("race_name", "Grand Prix").replace("Grand Prix", "GP")
        name_trunc = self._truncate(draw, race_name, self.fonts["detail"],
                                    self.display_width - 42)
        self._draw_text_outlined(draw, (3, 1), name_trunc, self.fonts["detail"], fill=F1_RED)
        if race.get("date"):
            try:
                raw = race["date"]
                dt = self._to_local_dt(raw + "T12:00:00Z" if "T" not in raw else raw)
                date_str = dt.strftime("%b %d").upper()
                dw = self._tw(draw, date_str, self.fonts["small"])
                self._draw_text_outlined(draw, (self.display_width - dw - 2, 1),
                                         date_str, self.fonts["small"], fill=(110, 110, 110))
            except (ValueError, TypeError):
                pass

        self._draw_accent_bar(draw, cid)
        x = self.accent_bar_width + 3
        content_y = header_h + 3

        # ── Team logo (right side) ──────────────────────────────────────
        logo = self.logo_loader.get_team_logo(
            cid,
            max_height=self.display_height - header_h - 4,
            max_width=int(self.display_width * 0.20))
        logo_left = self.display_width
        if logo:
            logo_x = self.display_width - logo.width - 3
            logo_y = header_h + (self.display_height - header_h - logo.height) // 2
            img.paste(logo, (logo_x, logo_y), logo)
            logo_left = logo_x - 3

        # ── Row 1: position + driver code ──────────────────────────────
        pos_label = f"P{pos}"
        pos_color = (180, 180, 180)
        self._draw_text_outlined(draw, (x, content_y), pos_label,
                                 self.fonts["position"], fill=pos_color)
        pos_w = self._tw(draw, pos_label, self.fonts["position"])

        code_x = x + pos_w + 4
        code_trunc = self._truncate(draw, code, self.fonts["position"],
                                    logo_left - code_x - 40)
        self._draw_text_outlined(draw, (code_x, content_y), code_trunc,
                                 self.fonts["position"], fill=tc_bright)

        # Gap / status (right-aligned before logo)
        status = result.get("status", "")
        if self.show_dnf_status and status and status not in ("Finished", ""):
            if status.startswith("+") and "Lap" in status:
                parts = status.split()
                gap_str = f"+{parts[0].lstrip('+')}L"
                gap_color = (180, 120, 40)
            else:
                gap_str = "RET"
                gap_color = (180, 60, 60)
        else:
            gap_str = result.get("time", result.get("gap", ""))
            gap_color = (255, 190, 50)

        if gap_str:
            code_right = code_x + self._tw(draw, code_trunc, self.fonts["position"]) + 4
            avail_gap = logo_left - code_right
            if avail_gap > 18:
                gap_trunc = self._truncate(draw, gap_str, self.fonts["small"], avail_gap)
                g_x = logo_left - self._tw(draw, gap_trunc, self.fonts["small"])
                if g_x > code_right:
                    draw.text((g_x, content_y + 2), gap_trunc,
                              font=self.fonts["small"], fill=gap_color)

        # ── Row 2: position delta + points scored ──────────────────────
        row2_y = content_y + self._th(draw, pos_label, self.fonts["position"]) + 2
        if row2_y + 4 < self.display_height - 2:
            cur_x = x
            # Position delta
            grid_pos = result.get("grid", 0)
            if self.show_position_delta and grid_pos > 0:
                delta = grid_pos - pos
                if delta > 0:
                    delta_str, delta_color = f"+{delta}", (0, 210, 80)
                elif delta < 0:
                    delta_str, delta_color = str(delta), (220, 50, 50)
                else:
                    delta_str, delta_color = "=0", (120, 120, 120)
                draw.text((cur_x, row2_y), delta_str,
                          font=self.fonts["small"], fill=delta_color)
                cur_x += self._tw(draw, delta_str, self.fonts["small"]) + 4

            # Points scored this race
            pts = result.get("points", 0)
            if pts > 0:
                pts_str = f"+{int(pts)}pts"
                pts_color = (255, 220, 50)
            else:
                pts_str = "0pts"
                pts_color = (80, 80, 80)
            if cur_x + self._tw(draw, pts_str, self.fonts["small"]) < logo_left:
                draw.text((cur_x, row2_y), pts_str,
                          font=self.fonts["small"], fill=pts_color)

        # Team color bottom bar
        draw.rectangle([0, self.display_height - 2,
                         self.display_width - 1, self.display_height - 1],
                        fill=tuple(max(0, int(c * 0.6)) for c in tc))

        return img

    # ─── Race Points Haul Card ─────────────────────────────────────────

    def render_race_points_haul(self, race: Dict, top_n: int = 5) -> Image.Image:
        """
        Bar chart of points scored per driver in a race.
        Uses race["all_results"] (full 20-driver list) when available,
        falling back to race["results"].
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        all_results = race.get("all_results", race.get("results", []))
        # Only drivers who scored points, sorted by points descending
        point_scorers = sorted(
            [r for r in all_results if float(r.get("points", 0)) > 0],
            key=lambda r: float(r.get("points", 0)),
            reverse=True)[:top_n]

        race_name = race.get("race_name", "Grand Prix")
        short_name = race_name.replace("Grand Prix", "GP")

        # Header
        header_h = self._th(draw, "A", self.fonts["detail"]) + 1
        draw.rectangle([0, 0, self.display_width - 1, header_h + 1], fill=(20, 0, 0))
        title = f"{short_name} PTS"
        title_trunc = self._truncate(draw, title, self.fonts["detail"],
                                     self.display_width - 4)
        self._draw_text_outlined(draw, (3, 1), title_trunc,
                                 self.fonts["detail"], fill=F1_RED)

        if not point_scorers:
            return img

        content_y = header_h + 2
        content_h = self.display_height - content_y - 1
        n = len(point_scorers)
        row_h = max(4, content_h // n)

        max_pts = float(point_scorers[0].get("points", 25))
        if max_pts <= 0:
            max_pts = 25.0

        # Layout columns
        accent_w = 2
        code_x = accent_w + 2
        code_field_w = self._tw(draw, "NOR", self.fonts["small"]) + 1
        pts_str_max = self._tw(draw, "26", self.fonts["small"]) + 2
        bar_x0 = code_x + code_field_w + 2
        bar_x1 = self.display_width - pts_str_max - 2
        bar_available = max(1, bar_x1 - bar_x0)

        for i, r in enumerate(point_scorers):
            row_y = content_y + i * row_h
            if row_y >= self.display_height - 1:
                break

            cid = r.get("constructor_id", "")
            tc = get_team_color(cid)
            tc_bright = _team_color_bright(cid, min_max=120)
            pts = float(r.get("points", 0))
            code = r.get("code", "???")

            row_bot = min(row_y + row_h - 1, self.display_height - 2)

            # Team accent bar on left
            draw.rectangle([0, row_y, accent_w - 1, row_bot], fill=tc)

            # Driver code
            code_trunc = self._truncate(draw, code, self.fonts["small"], code_field_w)
            text_h = self._th(draw, "A", self.fonts["small"])
            cy = row_y + max(0, (row_h - text_h) // 2)
            draw.text((code_x, cy), code_trunc, font=self.fonts["small"], fill=tc_bright)

            # Points bar (team color fill)
            fill_ratio = pts / max_pts
            fill_w = max(1, int(bar_available * fill_ratio))
            bar_top = row_y + 1
            bar_bot = row_bot - 1
            if bar_bot > bar_top:
                bar_color = tuple(max(0, int(c * 0.55)) for c in tc)
                draw.rectangle([bar_x0, bar_top, bar_x0 + fill_w, bar_bot],
                               fill=bar_color)

            # Points number right-aligned
            pts_int = int(pts) if pts == int(pts) else pts
            pts_str = str(pts_int)
            pts_w = self._tw(draw, pts_str, self.fonts["small"])
            pts_x = self.display_width - pts_w - 2
            draw.text((pts_x, cy), pts_str, font=self.fonts["small"], fill=(200, 200, 200))

            # Row separator
            if i < n - 1 and row_y + row_h < self.display_height - 1:
                draw.line([(2, row_y + row_h - 1),
                           (self.display_width - 2, row_y + row_h - 1)],
                          fill=(25, 25, 25))

        return img

    # ─── Recent Race Winners Summary Card ─────────────────────────────

    def render_recent_winners_card(self, recent_races: List[Dict]) -> Image.Image:
        """
        Compact summary card: one row per recent race showing the winner.
        Row: team accent bar | race short name | team name | winner code.
        Shown at the start of the recent races scroll section.
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        n = len(recent_races)

        # Header
        header_h = self._th(draw, "A", self.fonts["detail"]) + 1
        draw.rectangle([0, 0, self.display_width - 1, header_h + 1], fill=(20, 0, 0))
        title = f"WINNERS L{n}"
        title_w = self._tw(draw, title, self.fonts["detail"])
        self._draw_text_outlined(draw, ((self.display_width - title_w) // 2, 1),
                                 title, self.fonts["detail"], fill=F1_RED)

        content_y = header_h + 2
        content_h = self.display_height - content_y - 1
        row_h = max(5, content_h // max(1, n))

        for i, race in enumerate(recent_races):
            row_y = content_y + i * row_h
            if row_y >= self.display_height - 2:
                break

            results = race.get("results", [])
            if not results:
                continue

            winner = results[0]
            cid = winner.get("constructor_id", "")
            code = winner.get("code", "???")
            tc = get_team_color(cid)
            tc_bright = _team_color_bright(cid, min_max=130)

            row_bot = min(row_y + row_h - 1, self.display_height - 2)

            # Team accent bar
            draw.rectangle([0, row_y, 2, row_bot], fill=tc)

            # Race name (short)
            race_name = race.get("race_name", "")
            short = (race_name
                     .replace("Grand Prix", "GP")
                     .replace("Las Vegas", "LV")
                     .replace("Abu Dhabi", "Abu D")
                     .replace("Saudi Arabian", "Saudi"))

            text_h = self._th(draw, "A", self.fonts["small"])
            cy = row_y + max(0, (row_h - text_h) // 2)

            # Right side: winner code then team name (left of code)
            code_w = self._tw(draw, code, self.fonts["small"])
            code_x = self.display_width - code_w - 2
            draw.text((code_x, cy), code, font=self.fonts["small"], fill=tc_bright)

            team_abbr = _team_short(cid)
            team_dim = tuple(max(0, int(c * 0.55)) for c in tc_bright)
            team_w = self._tw(draw, team_abbr, self.fonts["small"])
            team_x = code_x - team_w - 3

            # Race name on left
            race_max_w = max(10, team_x - 6)
            race_trunc = self._truncate(draw, short, self.fonts["small"], race_max_w)
            draw.text((4, cy), race_trunc, font=self.fonts["small"], fill=(140, 140, 140))

            # Team name between race and code (if it fits)
            race_right = 4 + self._tw(draw, race_trunc, self.fonts["small"]) + 2
            if team_x > race_right:
                draw.text((team_x, cy), team_abbr, font=self.fonts["small"], fill=team_dim)

            # Row separator
            if i < n - 1 and row_y + row_h < self.display_height - 1:
                draw.line([(2, row_y + row_h - 1),
                           (self.display_width - 2, row_y + row_h - 1)],
                          fill=(25, 25, 25))

        return img

    # ─── Shared Driver Row ─────────────────────────────────────────────

    def _render_driver_row(self, entry: Dict, time_key: str = "",
                           gap_key: str = "", show_eliminated: bool = False,
                           session_label: str = "") -> Image.Image:
        """Driver row used by qualifying, practice, sprint."""
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        cid = entry.get("constructor_id", "")
        tc = get_team_color(cid)
        self._draw_accent_bar(draw, cid)
        x = self.accent_bar_width + 2

        content_h = self.display_height
        content_max_x = self.display_width - (self.logo_max + 4)

        # Row 1: pos + code
        pos_text = f"P{entry.get('position', '?')}"
        self._draw_text_outlined(draw, (x, 2), pos_text, self.fonts["position"],
                                 fill=(200, 200, 200))
        px = x + self._tw(draw, pos_text, self.fonts["position"]) + 3

        code = entry.get("code", "???")
        self._draw_text_outlined(draw, (px, 2), code, self.fonts["position"],
                                 fill=(255, 255, 255))
        cx = px + self._tw(draw, code, self.fonts["position"]) + 4

        # Time — strip milliseconds (e.g. "1:10.123" → "1:10") so the dot isn't invisible
        time_str = entry.get(time_key, "") if time_key else ""
        if time_str and "." in time_str and not time_str.startswith("+"):
            time_str = time_str.rsplit(".", 1)[0]
        if time_str:
            time_trunc = self._truncate(draw, time_str, self.fonts["detail"],
                                        content_max_x - cx)
            self._draw_text_outlined(draw, (cx, 3), time_trunc, self.fonts["detail"],
                                     fill=(200, 200, 200))
        elif show_eliminated:
            elim = entry.get("eliminated_in", "")
            if elim:
                self._draw_text_outlined(draw, (cx, 3), "OUT", self.fonts["detail"],
                                         fill=(220, 60, 60))

        # Gap below time
        gap_str = entry.get(gap_key, "") if gap_key else ""
        row2_y = 2 + self._th(draw, pos_text, self.fonts["position"]) + 2
        if gap_str and row2_y + 5 < content_h:
            gap_trunc = self._truncate(draw, gap_str, self.fonts["small"],
                                       content_max_x - x)
            draw.text((x + self._tw(draw, pos_text, self.fonts["position"]) + 3,
                       row2_y), gap_trunc, font=self.fonts["small"],
                      fill=(255, 200, 50))

        # Team logo right-aligned
        logo = self.logo_loader.get_team_logo(
            cid, max_height=int(content_h * 0.65), max_width=int(content_h * 0.65))
        if logo:
            lx = self.display_width - logo.width - 2
            ly = (content_h - logo.height) // 2
            img.paste(logo, (lx, ly), logo)

        # Bottom team color accent line
        draw.rectangle([self.accent_bar_width, content_h - 2,
                        self.display_width - 1, content_h - 1],
                       fill=tuple(max(0, int(c * 0.4)) for c in tc))

        return img

    # ─── Qualifying Team H2H Card ──────────────────────────────────────

    def render_qualifying_team_duel_card(self, qualifying: Dict) -> Image.Image:
        """
        Compact card showing intra-team qualifying battles for all constructors.
        Each row: team accent bar | team name | winner code | vs | loser code | Δpos
        Winner rendered in bright team color, loser dimmed.
        Teams sorted by their best qualifier's position.
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        results = qualifying.get("results", [])
        race_name = qualifying.get("race_name", "")
        short_name = race_name.replace("Grand Prix", "GP")

        # Group results by constructor; positions already in order
        team_map: Dict[str, List[Dict]] = {}
        for r in results:
            cid = r.get("constructor_id", "")
            if cid:
                team_map.setdefault(cid, []).append(r)

        # Build duels sorted by the better driver's position
        duels = []
        for cid, entries in team_map.items():
            if len(entries) < 2:
                continue
            e1, e2 = sorted(entries, key=lambda e: e.get("position", 99))[:2]
            # e1 = better qualifier (lower position number)
            pos1 = e1.get("position", 99)
            pos2 = e2.get("position", 99)
            duels.append((pos1, cid, e1, e2, pos2 - pos1))

        duels.sort(key=lambda d: d[0])

        # Header
        header_h = self._th(draw, "A", self.fonts["detail"]) + 1
        draw.rectangle([0, 0, self.display_width - 1, header_h + 1], fill=(15, 0, 30))
        hdr_text = f"QUALI H2H"
        if short_name:
            hdr_text = f"{short_name} H2H"
        hdr_w = self._tw(draw, hdr_text, self.fonts["detail"])
        self._draw_text_outlined(draw, ((self.display_width - hdr_w) // 2, 1),
                                 hdr_text, self.fonts["detail"], fill=(180, 80, 255))

        content_y = header_h + 2
        content_h = self.display_height - content_y - 1
        n_teams = min(len(duels), 5)
        if n_teams == 0:
            return img

        row_h = max(4, content_h // n_teams)
        text_h = self._th(draw, "A", self.fonts["small"])

        for i, (pos1, cid, winner, loser, delta) in enumerate(duels[:n_teams]):
            row_y = content_y + i * row_h
            if row_y >= self.display_height - 1:
                break

            tc = get_team_color(cid)
            tc_bright = _team_color_bright(cid, min_max=130)
            tc_dim = tuple(max(0, int(c * 0.45)) for c in tc_bright)
            row_bot = min(row_y + row_h - 1, self.display_height - 2)

            # Team accent bar
            draw.rectangle([0, row_y, 1, row_bot], fill=tc)

            cy = row_y + max(0, (row_h - text_h) // 2)

            # Layout: [acc][team_abbr][winner_code][>][loser_code][delta]
            x = 3
            team_abbr = _team_short(cid)[:5]  # up to 5 chars to keep it compact
            team_max = self._tw(draw, "RSTM.", self.fonts["small"]) + 2
            team_trunc = self._truncate(draw, team_abbr, self.fonts["small"], team_max)
            draw.text((x, cy), team_trunc, font=self.fonts["small"], fill=tc_dim)
            x += team_max + 2

            w_code = winner.get("code", "???")
            draw.text((x, cy), w_code, font=self.fonts["small"], fill=tc_bright)
            x += self._tw(draw, "NOR", self.fonts["small"]) + 2

            # ">" separator
            draw.text((x, cy), ">", font=self.fonts["small"], fill=(60, 60, 60))
            x += self._tw(draw, ">", self.fonts["small"]) + 2

            l_code = loser.get("code", "???")
            draw.text((x, cy), l_code, font=self.fonts["small"], fill=tc_dim)
            x += self._tw(draw, "NOR", self.fonts["small"]) + 3

            # Position delta (how many spots ahead)
            if delta > 0:
                delta_str = f"+{delta}"
                draw.text((x, cy), delta_str, font=self.fonts["small"], fill=(100, 100, 100))

            # Row separator
            if i < n_teams - 1 and row_y + row_h < self.display_height - 1:
                draw.line([(2, row_y + row_h - 1),
                           (self.display_width - 2, row_y + row_h - 1)],
                          fill=(25, 25, 25))

        return img

    # ─── Qualifying Card ───────────────────────────────────────────────

    def render_qualifying_entry(self, entry: Dict, session_label: str = "Q3") -> Image.Image:
        session_key = session_label.lower()
        return self._render_driver_row(entry, time_key=session_key,
                                       gap_key=f"{session_key}_gap",
                                       show_eliminated=True, session_label=session_label)

    def render_qualifying_header(self, session_label: str = "Q3", race_name: str = "") -> Image.Image:
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)
        # Top bar
        draw.rectangle([0, 0, self.display_width - 1, self.display_height // 2], fill=(15, 0, 30))

        f1_logo = self.logo_loader.get_f1_logo(
            max_height=int(self.display_height * 0.4),
            max_width=int(self.display_width * 0.12))
        hx = 2
        if f1_logo:
            img.paste(f1_logo, (2, 2), f1_logo)
            hx = f1_logo.width + 5

        txt = f"QUALIFYING - {session_label}"
        self._draw_text_outlined(draw, (hx, 2), txt, self.fonts["detail"], fill=(255, 215, 0))

        if race_name:
            ry = 2 + self._th(draw, txt, self.fonts["detail"]) + 2
            short = race_name.replace("Grand Prix", "GP")
            short = self._truncate(draw, short, self.fonts["small"], self.display_width - 4)
            if ry + 5 < self.display_height:
                self._draw_text_outlined(draw, (2, ry), short, self.fonts["small"],
                                         fill=(160, 160, 160))
        return img

    # ─── Practice Card ─────────────────────────────────────────────────

    def render_practice_entry(self, entry: Dict) -> Image.Image:
        return self._render_driver_row(entry, time_key="best_lap", gap_key="gap")

    def render_practice_header(self, session_name: str = "FP3", circuit: str = "") -> Image.Image:
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, self.display_width - 1, self.display_height // 2], fill=(0, 20, 10))

        f1_logo = self.logo_loader.get_f1_logo(
            max_height=int(self.display_height * 0.4),
            max_width=int(self.display_width * 0.12))
        hx = 2
        if f1_logo:
            img.paste(f1_logo, (2, 2), f1_logo)
            hx = f1_logo.width + 5

        label = f"FREE PRACTICE {session_name[-1]}" if len(session_name) == 3 else session_name
        self._draw_text_outlined(draw, (hx, 2), label, self.fonts["detail"], fill=(80, 220, 120))

        if circuit:
            cy = 2 + self._th(draw, label, self.fonts["detail"]) + 2
            c_trunc = self._truncate(draw, circuit, self.fonts["small"], self.display_width - 4)
            if cy + 5 < self.display_height:
                self._draw_text_outlined(draw, (2, cy), c_trunc, self.fonts["small"],
                                         fill=(160, 160, 160))
        return img

    # ─── Sprint Card ───────────────────────────────────────────────────

    def render_sprint_entry(self, entry: Dict) -> Image.Image:
        return self._render_driver_row(entry, time_key="time")

    def render_sprint_header(self, race_name: str = "") -> Image.Image:
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, self.display_width - 1, self.display_height // 2], fill=(30, 10, 0))

        f1_logo = self.logo_loader.get_f1_logo(
            max_height=int(self.display_height * 0.4),
            max_width=int(self.display_width * 0.12))
        hx = 2
        if f1_logo:
            img.paste(f1_logo, (2, 2), f1_logo)
            hx = f1_logo.width + 5

        self._draw_text_outlined(draw, (hx, 2), "SPRINT RACE", self.fonts["detail"],
                                 fill=(255, 120, 0))

        if race_name:
            ry = 2 + self._th(draw, "A", self.fonts["detail"]) + 2
            short = race_name.replace("Grand Prix", "GP")
            short = self._truncate(draw, short, self.fonts["small"], self.display_width - 4)
            if ry + 5 < self.display_height:
                self._draw_text_outlined(draw, (2, ry), short, self.fonts["small"],
                                         fill=(160, 160, 160))
        return img

    # ─── Upcoming Race Card ────────────────────────────────────────────

    def render_upcoming_race(self, race: Dict) -> Image.Image:
        """
        Layout:
          Left zone (60% width): GP name · location · next session · countdown
          Right zone (40% width): Circuit outline image
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        # Circuit image on RIGHT
        circuit_img = None
        if self.show_circuit_map:
            circuit_img = self.logo_loader.get_circuit_image(
                circuit_name=race.get("circuit_name", ""),
                city=race.get("city", ""),
                max_height=self.display_height - 4,
                max_width=int(self.display_width * 0.38))

        if circuit_img:
            cx = self.display_width - circuit_img.width - 2
            cy = (self.display_height - circuit_img.height) // 2
            img.paste(circuit_img, (cx, cy), circuit_img)
            text_max_x = cx - 3
        else:
            text_max_x = self.display_width - 2

        # Red left accent stripe
        draw.rectangle([0, 0, 2, self.display_height - 1], fill=F1_RED)
        x = 4
        y = 1

        # Headline: short country/location name that fits in PressStart2P at 8px
        _COUNTRY_DISPLAY = {
            "United Kingdom": "BRITAIN",
            "United States": "USA",
            "Saudi Arabia": "SAUDI",
            "United Arab Emirates": "ABU DHABI",
            "UAE": "ABU DHABI",
            "Netherlands": "DUTCH",
            "Azerbaijan": "BAKU",
        }
        country = race.get("country", "")
        city = race.get("city", "")
        race_name = race.get("short_name", race.get("name", ""))

        if country:
            headline = _COUNTRY_DISPLAY.get(country, country).upper()
        else:
            # Strip "Grand Prix" — gives "Canadian", "Monaco", etc.
            headline = race_name.replace("Grand Prix", "").replace("GP", "").strip().upper()

        headline = self._truncate(draw, headline, self.fonts["header"], text_max_x - x)
        self._draw_text_outlined(draw, (x, y), headline, self.fonts["header"],
                                 fill=(255, 255, 255))
        y += self._th(draw, headline, self.fonts["header"]) + 2

        # GP name + city on row 2 (compact, detail font)
        gp_short = race_name.replace("Grand Prix", "GP")
        if city and city.lower() not in gp_short.lower():
            gp_short = f"{gp_short}"
        if gp_short and y + 5 < self.display_height - 12:
            loc_trunc = self._truncate(draw, gp_short, self.fonts["small"], text_max_x - x)
            self._draw_text_outlined(draw, (x, y), loc_trunc, self.fonts["small"],
                                     fill=(120, 120, 120))
            y += self._th(draw, "A", self.fonts["small"]) + 1

        # Next session time
        next_type = race.get("next_session_type", "")
        sessions = race.get("sessions", [])
        next_date = ""
        for sess in sessions:
            if sess.get("type_abbr") == next_type and sess.get("date"):
                next_date = sess["date"]
                break

        if next_type and next_date and y + 5 < self.display_height - 8:
            abbrs = {"FP1": "FP1", "FP2": "FP2", "FP3": "FP3",
                     "Qual": "QUALI", "Race": "RACE", "SS": "S.Q", "SR": "SPR"}
            sess_label = abbrs.get(next_type, next_type)
            try:
                dt = self._to_local_dt(next_date)
                time_str = dt.strftime("%a %I:%M%p").upper().lstrip("0")
                next_line = f"{sess_label}: {time_str}"
            except (ValueError, TypeError):
                next_line = f"NEXT: {sess_label}"

            next_line = self._truncate(draw, next_line, self.fonts["small"], text_max_x - x)
            self._draw_text_outlined(draw, (x, y), next_line, self.fonts["small"],
                                     fill=(80, 200, 255))
            y += self._th(draw, "A", self.fonts["small"]) + 1

        # Countdown (bottom, green)
        countdown = race.get("countdown_seconds")
        if countdown is not None and countdown >= 0:
            cnt_y = self.display_height - self._th(draw, "A", self.fonts["detail"]) - 2
            if countdown < 3600:
                sess_type = race.get("next_session_type", "Race")
                labels = {"Race": "RACE DAY!", "Qual": "QUALIFYING", "FP1": "FP1 SOON",
                          "FP2": "FP2 SOON", "FP3": "FP3 SOON", "SS": "S.QUALI", "SR": "SPRINT"}
                label = labels.get(sess_type, "RACE DAY!")
                pulse = max(150, min(255, int(180 + 75 * math.sin(time.time() * 3))))
                label = self._truncate(draw, label, self.fonts["detail"], text_max_x - x)
                self._draw_text_outlined(draw, (x, cnt_y), label, self.fonts["detail"],
                                         fill=(pulse, pulse, 0))
            else:
                d = int(countdown // 86400)
                h = int((countdown % 86400) // 3600)
                m = int((countdown % 3600) // 60)
                ct = f"{d}D {h}H {m}M" if d > 0 else f"{h}H {m}M"
                ct = self._truncate(draw, ct, self.fonts["detail"], text_max_x - x)
                self._draw_text_outlined(draw, (x, cnt_y), ct, self.fonts["detail"],
                                         fill=(50, 230, 80))

        return img

    # ─── Circuit Info Card ────────────────────────────────────────────

    def render_circuit_info_card(self, race: Dict) -> Image.Image:
        """
        Static circuit facts card: laps, length, total distance, lap record.
        Shown after the upcoming race card in the scroll sequence.

        Layout (128×32 example):
          [acc] MONACO              78 laps
                3.337 km/lap  ·  260.3 km
                REC 1:10.166  HAM  2021
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        circuit_name = race.get("circuit_name", "")
        city = race.get("city", "")
        info = get_circuit_info(circuit_name, city)

        if not info:
            # Fallback: just show "CIRCUIT DATA N/A" centered
            self._draw_accent_bar(draw, "")
            x = self.accent_bar_width + 3
            draw.text((x, self.display_height // 2 - 3), "CIRCUIT DATA N/A",
                      font=self.fonts["small"], fill=(80, 80, 80))
            return img

        # Red left accent bar
        draw.rectangle([0, 0, self.accent_bar_width - 1, self.display_height - 1],
                       fill=F1_RED)
        x = self.accent_bar_width + 3

        laps = info["laps"]
        length_km = info["length_km"]
        total_km = round(laps * length_km, 1)

        # ── Row 1: circuit display name (left) + laps (right) ────────
        name_disp = info["name"]
        laps_str = f"{laps}L"
        laps_w = self._tw(draw, laps_str, self.fonts["detail"])
        name_max_w = self.display_width - x - laps_w - 4
        name_trunc = self._truncate(draw, name_disp, self.fonts["detail"], name_max_w)
        self._draw_text_outlined(draw, (x, 2), name_trunc, self.fonts["detail"],
                                 fill=(255, 220, 0))
        draw.text((self.display_width - laps_w - 2, 2), laps_str,
                  font=self.fonts["detail"], fill=(180, 180, 180))

        row1_h = self._th(draw, name_trunc, self.fonts["detail"])

        # ── Row 2: per-lap distance · total race distance ─────────────
        row2_y = 2 + row1_h + 2
        km_str = f"{length_km}km"
        total_str = f"{total_km}km"
        sep_str = "  "
        full_str = km_str + sep_str + total_str
        if self._tw(draw, full_str, self.fonts["small"]) <= self.display_width - x - 2:
            draw.text((x, row2_y), km_str, font=self.fonts["small"], fill=(140, 200, 255))
            draw.text((x + self._tw(draw, km_str + sep_str, self.fonts["small"]), row2_y),
                      total_str, font=self.fonts["small"], fill=(80, 80, 80))
        else:
            draw.text((x, row2_y), km_str, font=self.fonts["small"], fill=(140, 200, 255))

        row2_h = self._th(draw, "A", self.fonts["small"])

        # ── Row 3: lap record ─────────────────────────────────────────
        row3_y = row2_y + row2_h + 2
        if row3_y + 4 < self.display_height - 2:
            rec_time = info.get("record_time", "")
            rec_driver = info.get("record_driver", "")
            rec_year = info.get("record_year", "")
            if rec_time:
                rec_label = "REC"
                rec_label_w = self._tw(draw, rec_label, self.fonts["small"])
                draw.text((x, row3_y), rec_label,
                          font=self.fonts["small"], fill=(80, 80, 80))
                rx = x + rec_label_w + 3
                draw.text((rx, row3_y), rec_time,
                          font=self.fonts["small"], fill=(255, 180, 0))
                rx += self._tw(draw, rec_time, self.fonts["small"]) + 4
                if rec_driver:
                    draw.text((rx, row3_y), rec_driver,
                              font=self.fonts["small"], fill=(200, 200, 200))
                    rx += self._tw(draw, rec_driver, self.fonts["small"]) + 3
                if rec_year and rx + self._tw(draw, str(rec_year), self.fonts["small"]) < self.display_width - 2:
                    draw.text((rx, row3_y), f"'{str(rec_year)[-2:]}",
                              font=self.fonts["small"], fill=(80, 80, 80))

        # Bottom red accent line
        draw.rectangle([self.accent_bar_width, self.display_height - 2,
                         self.display_width - 1, self.display_height - 1],
                        fill=(60, 0, 0))

        return img

    # ─── Calendar Entry Card ──────────────────────────────────────────

    def render_calendar_entry(self, entry: Dict) -> Image.Image:
        """
        Layout (two rows):
          Row 1: [DATE] [SESSION-TYPE colored]
          Row 2: [Event name (small)]
                 Time right-aligned on row 2
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        date_str = entry.get("date", "")
        date_disp = day_disp = ""
        if date_str:
            try:
                dt = self._to_local_dt(date_str)
                date_disp = dt.strftime("%b %d").upper()
                day_disp = dt.strftime("%a").upper()
            except (ValueError, TypeError):
                pass

        sess_type = entry.get("session_type", "")
        sess_colors = {
            "Race": (229, 30, 30), "Qual": (255, 200, 0), "Qualifying": (255, 200, 0),
            "FP1": (80, 210, 100), "FP2": (80, 210, 100), "FP3": (80, 210, 100),
            "SS": (255, 150, 0), "SR": (255, 100, 0),
            "Sprint": (255, 100, 0), "SprintQualifying": (255, 150, 0),
        }
        sess_labels = {"FP1": "FP1", "FP2": "FP2", "FP3": "FP3",
                       "Qual": "QUALI", "Qualifying": "QUALI",
                       "Race": "RACE", "SS": "S.Q", "SR": "SPRINT",
                       "Sprint": "SPRINT", "SprintQualifying": "S.QUALI"}
        sess_label = sess_labels.get(sess_type, sess_type)
        sess_color = sess_colors.get(sess_type, (180, 180, 180))

        # Row 1: date + session type
        x = 2
        if date_disp:
            self._draw_text_outlined(draw, (x, 2), date_disp, self.fonts["detail"],
                                     fill=(220, 220, 220))
            x += self._tw(draw, date_disp, self.fonts["detail"]) + 4

        if day_disp:
            self._draw_text_outlined(draw, (x, 2), day_disp, self.fonts["small"],
                                     fill=(100, 100, 100))
            x += self._tw(draw, day_disp, self.fonts["small"]) + 4

        if sess_label:
            # Badge background for session type
            sw = self._tw(draw, sess_label, self.fonts["detail"]) + 4
            sh = self._th(draw, sess_label, self.fonts["detail"]) + 2
            badge_color = tuple(max(0, int(c * 0.3)) for c in sess_color)
            draw.rectangle([x, 1, x + sw, 1 + sh], fill=badge_color)
            self._draw_text_outlined(draw, (x + 2, 2), sess_label, self.fonts["detail"],
                                     fill=sess_color, outline=(0, 0, 0))

        # Row 2: event name
        row2_y = 2 + self._th(draw, "A", self.fonts["detail"]) + 3
        if row2_y + 5 < self.display_height:
            event = entry.get("event_name", entry.get("name", "")).replace("Grand Prix", "GP")
            # Time right-aligned on row 2
            time_str = entry.get("status_detail", "")
            time_w = self._tw(draw, time_str, self.fonts["small"]) + 2 if time_str else 0

            event = self._truncate(draw, event, self.fonts["small"],
                                   self.display_width - 2 - time_w - 2)
            self._draw_text_outlined(draw, (2, row2_y), event, self.fonts["small"],
                                     fill=(150, 150, 150))

            if time_str:
                tx = self.display_width - time_w
                draw.text((tx, row2_y), time_str, font=self.fonts["small"], fill=(100, 100, 100))

        return img

    # ─── MY DRIVER Spotlight ─────────────────────────────────────────

    def render_favorite_driver_spotlight(
            self, driver_entry: Dict,
            is_live: bool = False, live_session: str = "",
            recent_races: List[Dict] = None) -> Image.Image:
        """
        Hero card for the tracked driver. Full width, rich team-color background.

        Layout:
          [thick team bar][LOGO][ CODE  P#  PTS · GAP ]
                                [ team name           ]
                                [ W · P    MY DRIVER  ]
          [═══════════════gap bar════════════════════]
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        cid = driver_entry.get("constructor_id", "")
        tc = get_team_color(cid)

        # Dual-zone gradient background
        lw = self.display_width * 2 // 3
        tint_l = tuple(max(0, int(c * 0.22)) for c in tc)
        tint_r = tuple(max(0, int(c * 0.08)) for c in tc)
        draw.rectangle([0, 0, lw, self.display_height - 1], fill=tint_l)
        draw.rectangle([lw, 0, self.display_width - 1, self.display_height - 1], fill=tint_r)

        # Gap bar
        gap_h = max(2, self.display_height // 16)
        content_h = self.display_height - gap_h
        if self.show_gap_bar:
            self._draw_gap_bar(draw, driver_entry, cid)

        # Thick accent bar
        accent_w = max(4, self.accent_bar_width + 2)
        draw.rectangle([0, 0, accent_w - 1, self.display_height - 1], fill=tc)
        x = accent_w + 2

        # Logo
        logo_sz = int(content_h * 0.88)
        logo = self.logo_loader.get_team_logo(cid, logo_sz, logo_sz)
        if logo:
            ly = (content_h - logo.height) // 2
            img.paste(logo, (x, ly), logo)
            x += logo.width + 3

        # ── Row 1: CODE (big, team color) + P# + PTS ─────────────
        code = driver_entry.get("code", "???")
        self._draw_text_outlined(draw, (x, 1), code, self.fonts["position"],
                                 fill=_team_color_bright(cid), outline=(0, 0, 0))
        cx = x + self._tw(draw, code, self.fonts["position"]) + 3

        pos_text = f"P{driver_entry.get('position', '?')}"
        self._draw_text_outlined(draw, (cx, 2), pos_text, self.fonts["small"],
                                 fill=(255, 215, 0))
        cx += self._tw(draw, pos_text, self.fonts["small"]) + 3

        pts = int(driver_entry.get("points", 0))
        pts_text = f"{pts}pt"
        pts_x = self.display_width - self._tw(draw, pts_text, self.fonts["detail"]) - 2
        self._draw_text_outlined(draw, (pts_x, 2), pts_text, self.fonts["detail"],
                                 fill=(255, 220, 50))

        # ── Row 2: Team name + GAP ───────────────────────────────
        row2_y = 1 + self._th(draw, code, self.fonts["position"]) + 2
        if row2_y + 5 < content_h:
            gap = driver_entry.get("gap_to_leader", 0)
            gap_text = f"-{int(gap)}" if gap > 0 else "LEADER"
            gap_x = self.display_width - self._tw(draw, gap_text, self.fonts["small"]) - 2
            draw.text((gap_x, row2_y), gap_text, font=self.fonts["small"],
                      fill=(255, 100, 100) if gap > 0 else (100, 255, 100))

            team_disp = _team_short(cid)
            team_disp = self._truncate(draw, team_disp, self.fonts["small"], gap_x - x - 2)
            self._draw_text_outlined(draw, (x, row2_y), team_disp, self.fonts["small"],
                                     fill=_team_color_bright(cid))

        # ── Row 3: Wins · Poles + recent form boxes + "MY DRIVER" badge ────
        row3_y = row2_y + self._th(draw, "A", self.fonts["small"]) + 2 if row2_y + 5 < content_h else row2_y
        if row3_y + 5 < content_h:
            wins = driver_entry.get("wins", 0)
            poles = driver_entry.get("poles", 0)
            wp_text = f"{wins}W  {poles}P"
            draw.text((x, row3_y), wp_text, font=self.fonts["small"], fill=(130, 130, 130))
            wp_right = x + self._tw(draw, wp_text, self.fonts["small"]) + 3

            badge_label = "MY DRIVER"
            badge_lw = self._tw(draw, badge_label, self.fonts["small"]) + 4
            badge_x = self.display_width - badge_lw - 1
            badge_col = tuple(max(0, int(c * 0.35)) for c in tc)
            draw.rectangle([badge_x, row3_y - 1, badge_x + badge_lw,
                            row3_y + self._th(draw, badge_label, self.fonts["small"]) + 1],
                           fill=badge_col)
            draw.text((badge_x + 2, row3_y), badge_label,
                      font=self.fonts["small"], fill=_team_color_bright(cid))

            # Recent form boxes: last N race positions for this driver
            if recent_races:
                drv_code = driver_entry.get("code", "")
                pos_history: List[Any] = []
                for rrace in recent_races:
                    all_results = rrace.get("all_results", rrace.get("results", []))
                    for rr in all_results:
                        if rr.get("code", "") == drv_code:
                            pos = rr.get("position", 0)
                            status = rr.get("status", "")
                            is_dnf = (status and status not in ("Finished", "")
                                      and not status.startswith("+"))
                            pos_history.append("DNF" if is_dnf else (int(pos) if pos else 20))
                            break

                box_sz = max(4, self._th(draw, "A", self.fonts["small"]))
                n_boxes = len(pos_history)
                boxes_w = n_boxes * (box_sz + 1)
                avail = badge_x - 4 - wp_right
                if n_boxes > 0 and boxes_w <= avail:
                    bx0 = badge_x - 4 - boxes_w
                    for j, pv in enumerate(pos_history):
                        bx = bx0 + j * (box_sz + 1)
                        if pv == "DNF":
                            bg, fg, lbl = (70, 15, 15), (200, 70, 70), "R"
                        elif pv == 1:
                            bg, fg, lbl = (55, 42, 0), (255, 210, 0), "1"
                        elif pv == 2:
                            bg, fg, lbl = (38, 38, 38), (195, 195, 195), "2"
                        elif pv == 3:
                            bg, fg, lbl = (45, 23, 0), (195, 110, 45), "3"
                        elif pv <= 10:
                            bg, fg, lbl = (0, 38, 18), (0, 170, 80), str(pv)
                        else:
                            bg, fg, lbl = (22, 22, 22), (90, 90, 90), str(pv)
                        draw.rectangle([bx, row3_y, bx + box_sz - 1, row3_y + box_sz - 1], fill=bg)
                        lw_ = self._tw(draw, lbl, self.fonts["small"])
                        lh_ = self._th(draw, lbl, self.fonts["small"])
                        draw.text((bx + max(0, (box_sz - lw_) // 2),
                                   row3_y + max(0, (box_sz - lh_) // 2)),
                                  lbl, font=self.fonts["small"], fill=fg)

        if is_live and live_session:
            self._draw_live_badge(draw, live_session)

        return img

    # ─── MY TEAM Spotlight ─────────────────────────────────────────────

    def render_favorite_team_spotlight(
            self, team_entry: Dict,
            driver_entries: List[Dict] = None,
            is_live: bool = False, live_session: str = "") -> Image.Image:
        """
        Hero card for the tracked constructor.

        Layout:
          [thick bar][LOGO][ TEAM NAME (big)     PTS  ]
                           [ P# · W              GAP  ]
                           [ MY TEAM badge            ]
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        cid = team_entry.get("constructor_id", "")
        tc = get_team_color(cid)

        tint = tuple(max(0, int(c * 0.18)) for c in tc)
        draw.rectangle([0, 0, self.display_width - 1, self.display_height - 1], fill=tint)

        gap_h = max(2, self.display_height // 16)
        content_h = self.display_height - gap_h
        if self.show_gap_bar:
            self._draw_gap_bar(draw, team_entry, cid)

        accent_w = max(4, self.accent_bar_width + 2)
        draw.rectangle([0, 0, accent_w - 1, self.display_height - 1], fill=tc)
        x = accent_w + 2

        logo_sz = int(content_h * 0.88)
        logo = self.logo_loader.get_team_logo(cid, logo_sz, logo_sz)
        if logo:
            ly = (content_h - logo.height) // 2
            img.paste(logo, (x, ly), logo)
            x += logo.width + 3

        # ── Row 1: Team name + PTS ───────────────────────────────
        pts = int(team_entry.get("points", 0))
        pts_text = f"{pts}pt"
        pts_w = self._tw(draw, pts_text, self.fonts["detail"]) + 2
        pts_x = self.display_width - pts_w

        team_name = _team_short(cid)
        team_name = self._truncate(draw, team_name, self.fonts["position"],
                                   pts_x - x - 2)
        self._draw_text_outlined(draw, (x, 1), team_name, self.fonts["position"],
                                 fill=_team_color_bright(cid), outline=(0, 0, 0))
        self._draw_text_outlined(draw, (pts_x, 2), pts_text, self.fonts["detail"],
                                 fill=(255, 220, 50))

        # ── Row 2: Position + Wins + GAP ────────────────────────
        row2_y = 1 + self._th(draw, team_name, self.fonts["position"]) + 2
        if row2_y + 5 < content_h:
            pos = team_entry.get("position", "?")
            wins = team_entry.get("wins", 0)
            gap = team_entry.get("gap_to_leader", 0)

            pw_text = f"P{pos}  {wins}W"
            self._draw_text_outlined(draw, (x, row2_y), pw_text, self.fonts["small"],
                                     fill=(200, 200, 200))

            if gap > 0:
                gap_text = f"-{int(gap)}"
                gx = self.display_width - self._tw(draw, gap_text, self.fonts["small"]) - 2
                draw.text((gx, row2_y), gap_text, font=self.fonts["small"], fill=(255, 100, 100))

        # ── Row 3: MY TEAM badge ─────────────────────────────────
        row3_y = row2_y + self._th(draw, "A", self.fonts["small"]) + 2 if row2_y + 5 < content_h else row2_y
        if row3_y + 5 < content_h:
            label = "MY TEAM"
            lw2 = self._tw(draw, label, self.fonts["small"]) + 4
            lx = self.display_width - lw2 - 1
            badge_col = tuple(max(0, int(c * 0.35)) for c in tc)
            draw.rectangle([lx, row3_y - 1, lx + lw2, row3_y + self._th(draw, label, self.fonts["small"]) + 1],
                           fill=badge_col)
            draw.text((lx + 2, row3_y), label, font=self.fonts["small"], fill=_team_color_bright(cid))

            # Show drivers if available
            if driver_entries:
                drivers_text = " · ".join(d.get("code", "") for d in driver_entries[:2])
                d_trunc = self._truncate(draw, drivers_text, self.fonts["small"], lx - x - 2)
                draw.text((x, row3_y), d_trunc, font=self.fonts["small"], fill=(100, 100, 100))

        if is_live and live_session:
            self._draw_live_badge(draw, live_session)

        return img

    # ─── Driver Form Guide Card ───────────────────────────────────────

    def render_driver_form_card(
            self, drivers: List[Dict], recent_races: List[Dict]) -> Image.Image:
        """
        2-column grid showing recent race positions for top drivers.
        Each position shown as a small colored box: gold(P1), silver(P2),
        bronze(P3), green(P4-10), gray(P11+), red(DNF).
        Races shown left=oldest, right=most recent.
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        # Build position history per driver code from all race results
        pos_history: Dict[str, List] = {}
        for race in recent_races:
            all_results = race.get("all_results", race.get("results", []))
            for r in all_results:
                code = r.get("code", "")
                if not code:
                    continue
                pos = r.get("position", 0)
                status = r.get("status", "")
                is_dnf = (status and status not in ("Finished", "")
                          and not status.startswith("+"))
                pos_val: Any = "DNF" if is_dnf else (int(pos) if pos else 20)
                pos_history.setdefault(code, []).append(pos_val)

        n_races = len(recent_races)

        # Header
        header_h = self._th(draw, "A", self.fonts["detail"]) + 1
        draw.rectangle([0, 0, self.display_width - 1, header_h + 1], fill=(15, 15, 20))
        title = f"FORM L{n_races}"
        title_w = self._tw(draw, title, self.fonts["detail"])
        draw.text(((self.display_width - title_w) // 2, 1),
                  title, font=self.fonts["detail"], fill=(100, 100, 100))

        content_y = header_h + 2
        content_h = self.display_height - content_y - 1

        n_rows = 4
        n_cols = 2
        row_h = max(4, content_h // n_rows)
        col_w = self.display_width // n_cols

        # Size of each position indicator box
        box_sz = max(4, row_h - 2)
        box_gap = 1
        code_field = self._tw(draw, "NOR", self.fonts["small"]) + 2

        display_drivers = drivers[: n_cols * n_rows]

        for i, driver in enumerate(display_drivers):
            col = i % n_cols
            row = i // n_cols
            x0 = col * col_w
            row_y = content_y + row * row_h

            code = driver.get("code", "")
            cid = driver.get("constructor_id", "")
            tc_bright = _team_color_bright(cid, min_max=110)

            text_h = self._th(draw, "A", self.fonts["small"])
            cy = row_y + max(0, (row_h - text_h) // 2)
            draw.text((x0 + 2, cy), code, font=self.fonts["small"], fill=tc_bright)

            history = pos_history.get(code, [])
            box_x = x0 + 2 + code_field

            for j, pos_val in enumerate(history[:n_races]):
                bx = box_x + j * (box_sz + box_gap)
                by = row_y + max(0, (row_h - box_sz) // 2)

                if pos_val == "DNF":
                    bg, fg, label = (70, 15, 15), (200, 70, 70), "R"
                elif pos_val == 1:
                    bg, fg, label = (55, 42, 0), (255, 210, 0), "1"
                elif pos_val == 2:
                    bg, fg, label = (38, 38, 38), (195, 195, 195), "2"
                elif pos_val == 3:
                    bg, fg, label = (45, 23, 0), (195, 110, 45), "3"
                elif pos_val <= 10:
                    bg, fg, label = (0, 38, 18), (0, 170, 80), str(pos_val)
                else:
                    bg, fg, label = (22, 22, 22), (90, 90, 90), str(pos_val)

                draw.rectangle([bx, by, bx + box_sz - 1, by + box_sz - 1], fill=bg)
                lbl_w = self._tw(draw, label, self.fonts["small"])
                lbl_h = self._th(draw, label, self.fonts["small"])
                lx = bx + max(0, (box_sz - lbl_w) // 2)
                ly = by + max(0, (box_sz - lbl_h) // 2)
                draw.text((lx, ly), label, font=self.fonts["small"], fill=fg)

            # Column divider
            if col == 0 and i + 1 < len(display_drivers):
                draw.line([(col_w, row_y), (col_w, row_y + row_h - 1)], fill=(30, 30, 30))

        return img

    # ─── Championship Leaders Card ────────────────────────────────────

    def render_championship_leaders(
            self, driver_leader: Dict, constructor_leader: Dict,
            is_live: bool = False, live_session: str = "") -> Image.Image:
        """
        Side-by-side: left = P1 driver, right = P1 constructor.
        Full height — no header bar. Small section labels at top of each half.
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        drv_id = driver_leader.get("constructor_id", "")
        con_id = constructor_leader.get("constructor_id", "")

        half_w = self.display_width // 2

        # Background tints
        drv_tint = tuple(max(0, int(c * 0.14)) for c in get_team_color(drv_id))
        con_tint = tuple(max(0, int(c * 0.14)) for c in get_team_color(con_id))
        draw.rectangle([0, 0, half_w - 1, self.display_height - 1], fill=drv_tint)
        draw.rectangle([half_w, 0, self.display_width - 1, self.display_height - 1], fill=con_tint)

        # Center divider
        draw.line([(half_w, 0), (half_w, self.display_height - 1)], fill=(50, 50, 50))

        label_h = self._th(draw, "A", self.fonts["detail"])

        # Small section labels at top of each half
        draw.text((3, 1), "DRIVER", font=self.fonts["detail"], fill=(80, 80, 80))
        con_label = "CONSTR."
        cl_w = self._tw(draw, con_label, self.fonts["detail"])
        draw.text((self.display_width - cl_w - 4, 1), con_label,
                  font=self.fonts["detail"], fill=(80, 80, 80))

        content_y = label_h + 3
        content_h = self.display_height - content_y

        # ── Left accent bar + logo ────────────────────────────────
        draw.rectangle([0, 0, 2, self.display_height - 1], fill=get_team_color(drv_id))

        logo_sz = max(6, int(content_h * 0.65))
        drv_logo = self.logo_loader.get_team_logo(drv_id, logo_sz, logo_sz)
        dx = 4
        if drv_logo:
            img.paste(drv_logo, (dx, content_y + (content_h - drv_logo.height) // 2), drv_logo)
            dx += drv_logo.width + 2

        # Driver code + pts
        drv_code = driver_leader.get("code", "???")
        drv_pts  = int(driver_leader.get("points", 0))
        drv_wins = int(driver_leader.get("wins", 0))
        max_drv_w = half_w - dx - 2
        code_trunc = self._truncate(draw, drv_code, self.fonts["position"], max_drv_w)
        self._draw_text_outlined(draw, (dx, content_y), code_trunc, self.fonts["position"],
                                 fill=_team_color_bright(drv_id))
        pts_y = content_y + self._th(draw, drv_code, self.fonts["position"]) + 1
        if pts_y + 5 < self.display_height:
            wins_text = f"{drv_pts}pt  {drv_wins}W"
            wins_text = self._truncate(draw, wins_text, self.fonts["small"], max_drv_w)
            self._draw_text_outlined(draw, (dx, pts_y), wins_text,
                                     self.fonts["small"], fill=(255, 220, 50))

        # ── Right accent bar + logo ───────────────────────────────
        draw.rectangle([self.display_width - 3, 0,
                        self.display_width - 1, self.display_height - 1],
                       fill=get_team_color(con_id))

        con_logo = self.logo_loader.get_team_logo(con_id, logo_sz, logo_sz)
        cx2 = half_w + 3
        if con_logo:
            img.paste(con_logo, (cx2, content_y + (content_h - con_logo.height) // 2), con_logo)
            cx2 += con_logo.width + 2

        # Constructor name + pts
        con_name = _team_short(con_id)
        con_pts  = int(constructor_leader.get("points", 0))
        con_wins = int(constructor_leader.get("wins", 0))
        max_con_w = self.display_width - cx2 - 5
        con_name_font = (self.fonts["position"]
                         if self._tw(draw, con_name, self.fonts["position"]) <= max_con_w
                         else self.fonts["detail"])
        con_name = self._truncate(draw, con_name, con_name_font, max_con_w)
        self._draw_text_outlined(draw, (cx2, content_y), con_name, con_name_font,
                                 fill=_team_color_bright(con_id))
        con_pts_y = content_y + self._th(draw, con_name, con_name_font) + 1
        if con_pts_y + 5 < self.display_height:
            con_stat = f"{con_pts}pt  {con_wins}W"
            con_stat = self._truncate(draw, con_stat, self.fonts["small"], max_con_w)
            self._draw_text_outlined(draw, (cx2, con_pts_y), con_stat,
                                     self.fonts["small"], fill=(255, 220, 50))

        if is_live and live_session:
            self._draw_live_badge(draw, live_session)

        return img

    # ─── Championship Battle Card ─────────────────────────────────────

    def render_championship_battle_card(
            self, p1: Dict, p2: Dict,
            remaining_races: int = 0,
            is_live: bool = False, live_session: str = "") -> Image.Image:
        """
        Driver championship title fight card.
        Left half = P1 (leader), right half = P2 (challenger).
        Bottom: gap in points + races remaining + closability bar.
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        p1_cid = p1.get("constructor_id", "")
        p2_cid = p2.get("constructor_id", "")
        p1_pts = int(p1.get("points", 0))
        p2_pts = int(p2.get("points", 0))
        gap = p1_pts - p2_pts

        # Max remaining points (rough: 26 per race incl bonus point)
        max_catchable = remaining_races * 26

        half_w = self.display_width // 2

        # Background tints
        p1_tc = get_team_color(p1_cid)
        p2_tc = get_team_color(p2_cid)
        draw.rectangle([0, 0, half_w - 1, self.display_height - 1],
                       fill=tuple(max(0, int(c * 0.12)) for c in p1_tc))
        draw.rectangle([half_w, 0, self.display_width - 1, self.display_height - 1],
                       fill=tuple(max(0, int(c * 0.12)) for c in p2_tc))

        # Accent bars: left edge P1, right edge P2
        draw.rectangle([0, 0, 2, self.display_height - 1], fill=p1_tc)
        draw.rectangle([self.display_width - 3, 0,
                         self.display_width - 1, self.display_height - 1], fill=p2_tc)

        # Center divider
        draw.line([(half_w, 0), (half_w, self.display_height - 1)], fill=(40, 40, 40))

        # "BATTLE" header label
        hdr_y = 1
        hdr_h = self._th(draw, "A", self.fonts["detail"]) + 1

        battle_label = "BATTLE"
        bl_w = self._tw(draw, battle_label, self.fonts["detail"])
        draw.text(((self.display_width - bl_w) // 2, hdr_y),
                  battle_label, font=self.fonts["detail"], fill=(80, 80, 80))

        # Gap bar height (drawn at bottom)
        bar_h = 3
        content_y = hdr_y + hdr_h + 1
        # Reserve: bottom 2px team bar + bar_h + 1px gap row spacing
        gap_row_h = self._th(draw, "A", self.fonts["small"])
        bar_y = self.display_height - bar_h
        gap_row_y = bar_y - gap_row_h - 1
        content_h = gap_row_y - content_y - 1

        # ── Left half: P1 driver ───────────────────────────────────
        logo_sz = max(6, int(content_h * 0.75))
        p1_logo = self.logo_loader.get_team_logo(p1_cid, logo_sz, logo_sz)
        dx = 4
        if p1_logo:
            ly = content_y + (content_h - p1_logo.height) // 2
            img.paste(p1_logo, (dx, ly), p1_logo)
            dx += p1_logo.width + 2

        p1_code = p1.get("code", "???")
        p1_max_w = half_w - dx - 3
        code_trunc = self._truncate(draw, p1_code, self.fonts["position"], p1_max_w)
        self._draw_text_outlined(draw, (dx, content_y), code_trunc,
                                 self.fonts["position"], fill=_team_color_bright(p1_cid))
        pts_y = content_y + self._th(draw, code_trunc, self.fonts["position"]) + 1
        if pts_y + 4 < gap_row_y:
            pts_str = f"{p1_pts}pt"
            self._draw_text_outlined(draw, (dx, pts_y), pts_str,
                                     self.fonts["small"], fill=(255, 220, 50))

        # ── Right half: P2 driver (mirrored — name left of logo) ──
        p2_code = p2.get("code", "???")
        p2_logo = self.logo_loader.get_team_logo(p2_cid, logo_sz, logo_sz)
        p2_logo_x = self.display_width - 3
        if p2_logo:
            p2_logo_x = self.display_width - p2_logo.width - 4
            ly = content_y + (content_h - p2_logo.height) // 2
            img.paste(p2_logo, (p2_logo_x, ly), p2_logo)

        p2_max_w = p2_logo_x - half_w - 4
        p2_trunc = self._truncate(draw, p2_code, self.fonts["position"], p2_max_w)
        p2_code_w = self._tw(draw, p2_trunc, self.fonts["position"])
        p2_code_x = p2_logo_x - p2_code_w - 2
        if p2_code_x < half_w + 3:
            p2_code_x = half_w + 3
        self._draw_text_outlined(draw, (p2_code_x, content_y), p2_trunc,
                                 self.fonts["position"], fill=_team_color_bright(p2_cid))
        pts_y2 = content_y + self._th(draw, p2_trunc, self.fonts["position"]) + 1
        if pts_y2 + 4 < gap_row_y:
            pts_str2 = f"{p2_pts}pt"
            pts_w2 = self._tw(draw, pts_str2, self.fonts["small"])
            self._draw_text_outlined(draw, (p2_logo_x - pts_w2 - 2, pts_y2),
                                     pts_str2, self.fonts["small"], fill=(255, 220, 50))

        # ── Gap row (center) ──────────────────────────────────────
        if gap >= 0:
            gap_str = f"-{gap}pts"
        else:
            gap_str = f"+{abs(gap)}pts"
        gap_w = self._tw(draw, gap_str, self.fonts["small"])
        gap_x = (self.display_width - gap_w) // 2
        draw.text((gap_x, gap_row_y), gap_str,
                  font=self.fonts["small"], fill=(220, 220, 220))

        # Races remaining (left side of gap row)
        if remaining_races > 0:
            races_str = f"{remaining_races}L"  # L = races Left
            draw.text((4, gap_row_y), races_str,
                      font=self.fonts["small"], fill=(100, 100, 100))

        # Clinched / alive indicator (right side of gap row)
        if max_catchable > 0:
            if gap > max_catchable:
                clinch_str = "WON"
                clinch_color = (0, 220, 80)
            elif gap > max_catchable * 0.75:
                clinch_str = "NEAR"
                clinch_color = (255, 180, 0)
            else:
                clinch_str = "ALIVE"
                clinch_color = (180, 180, 180)
            clinch_w = self._tw(draw, clinch_str, self.fonts["small"])
            draw.text((self.display_width - clinch_w - 4, gap_row_y),
                      clinch_str, font=self.fonts["small"], fill=clinch_color)

        # ── Closability gap bar ───────────────────────────────────
        bar_x0 = 3
        bar_x1 = self.display_width - 4
        bar_w = bar_x1 - bar_x0
        draw.rectangle([bar_x0, bar_y, bar_x1, bar_y + bar_h - 1], fill=(25, 25, 25))
        if max_catchable > 0:
            fill_ratio = min(1.0, gap / max_catchable)
            fill_w = int(bar_w * fill_ratio)
            if fill_w > 0:
                # P2's team color: large fill = hard to close, small fill = alive
                bar_fill = tuple(max(0, int(c * 0.7)) for c in p2_tc)
                draw.rectangle([bar_x0, bar_y, bar_x0 + fill_w, bar_y + bar_h - 1],
                               fill=bar_fill)
            # P1 team color accent at left edge of bar
            draw.rectangle([bar_x0, bar_y, bar_x0 + 2, bar_y + bar_h - 1],
                           fill=tuple(max(0, int(c * 0.9)) for c in p1_tc))

        if is_live and live_session:
            self._draw_live_badge(draw, live_session)

        return img

    def render_constructor_battle_card(
            self, p1: Dict, p2: Dict,
            remaining_races: int = 0,
            is_live: bool = False, live_session: str = "") -> Image.Image:
        """
        Constructor championship title fight card.
        Left half = P1 constructor (leader), right half = P2 (challenger).
        Bottom: gap in points + races remaining + closability bar.
        """
        img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)

        p1_cid = p1.get("constructor_id", "")
        p2_cid = p2.get("constructor_id", "")
        p1_pts = int(p1.get("points", 0))
        p2_pts = int(p2.get("points", 0))
        gap = p1_pts - p2_pts

        # Max points a constructor can score in one race (1-2 sweep + fastest lap)
        max_catchable = remaining_races * 44

        half_w = self.display_width // 2

        # Background tints
        p1_tc = get_team_color(p1_cid)
        p2_tc = get_team_color(p2_cid)
        draw.rectangle([0, 0, half_w - 1, self.display_height - 1],
                       fill=tuple(max(0, int(c * 0.12)) for c in p1_tc))
        draw.rectangle([half_w, 0, self.display_width - 1, self.display_height - 1],
                       fill=tuple(max(0, int(c * 0.12)) for c in p2_tc))

        # Accent bars: left edge P1, right edge P2
        draw.rectangle([0, 0, 2, self.display_height - 1], fill=p1_tc)
        draw.rectangle([self.display_width - 3, 0,
                         self.display_width - 1, self.display_height - 1], fill=p2_tc)

        # Center divider
        draw.line([(half_w, 0), (half_w, self.display_height - 1)], fill=(40, 40, 40))

        # "CONSTR" header label
        hdr_y = 1
        hdr_h = self._th(draw, "A", self.fonts["detail"]) + 1
        constr_label = "CONSTR"
        cl_w = self._tw(draw, constr_label, self.fonts["detail"])
        draw.text(((self.display_width - cl_w) // 2, hdr_y),
                  constr_label, font=self.fonts["detail"], fill=(80, 80, 80))

        # Gap bar height (drawn at bottom)
        bar_h = 3
        content_y = hdr_y + hdr_h + 1
        gap_row_h = self._th(draw, "A", self.fonts["small"])
        bar_y = self.display_height - bar_h
        gap_row_y = bar_y - gap_row_h - 1
        content_h = gap_row_y - content_y - 1

        logo_sz = max(8, content_h - 2)

        # ── Left half: P1 constructor ──────────────────────────────
        p1_logo = self.logo_loader.get_team_logo(p1_cid, logo_sz, logo_sz)
        p1_dx = 4
        if p1_logo:
            ly = content_y + (content_h - p1_logo.height) // 2
            img.paste(p1_logo, (p1_dx, ly), p1_logo)
            p1_dx += p1_logo.width + 2

        p1_name = _team_short(p1_cid)
        p1_max_w = half_w - p1_dx - 3
        name1_trunc = self._truncate(draw, p1_name, self.fonts["small"], p1_max_w)
        self._draw_text_outlined(draw, (p1_dx, content_y), name1_trunc,
                                 self.fonts["small"], fill=_team_color_bright(p1_cid))
        pts_y1 = content_y + self._th(draw, name1_trunc, self.fonts["small"]) + 1
        if pts_y1 + 4 < gap_row_y:
            pts_str1 = f"{p1_pts}pt"
            self._draw_text_outlined(draw, (p1_dx, pts_y1), pts_str1,
                                     self.fonts["small"], fill=(255, 220, 50))

        # ── Right half: P2 constructor (mirrored) ──────────────────
        p2_logo = self.logo_loader.get_team_logo(p2_cid, logo_sz, logo_sz)
        p2_logo_x = self.display_width - 3
        if p2_logo:
            p2_logo_x = self.display_width - p2_logo.width - 4
            ly = content_y + (content_h - p2_logo.height) // 2
            img.paste(p2_logo, (p2_logo_x, ly), p2_logo)

        p2_name = _team_short(p2_cid)
        p2_max_w = p2_logo_x - half_w - 4
        name2_trunc = self._truncate(draw, p2_name, self.fonts["small"], p2_max_w)
        name2_w = self._tw(draw, name2_trunc, self.fonts["small"])
        name2_x = p2_logo_x - name2_w - 2
        if name2_x < half_w + 3:
            name2_x = half_w + 3
        self._draw_text_outlined(draw, (name2_x, content_y), name2_trunc,
                                 self.fonts["small"], fill=_team_color_bright(p2_cid))
        pts_y2 = content_y + self._th(draw, name2_trunc, self.fonts["small"]) + 1
        if pts_y2 + 4 < gap_row_y:
            pts_str2 = f"{p2_pts}pt"
            pts_w2 = self._tw(draw, pts_str2, self.fonts["small"])
            self._draw_text_outlined(draw, (p2_logo_x - pts_w2 - 2, pts_y2),
                                     pts_str2, self.fonts["small"], fill=(255, 220, 50))

        # ── Gap row (center) ──────────────────────────────────────
        gap_str = f"-{gap}pts" if gap >= 0 else f"+{abs(gap)}pts"
        gap_w = self._tw(draw, gap_str, self.fonts["small"])
        gap_x = (self.display_width - gap_w) // 2
        draw.text((gap_x, gap_row_y), gap_str,
                  font=self.fonts["small"], fill=(220, 220, 220))

        if remaining_races > 0:
            races_str = f"{remaining_races}L"
            draw.text((4, gap_row_y), races_str,
                      font=self.fonts["small"], fill=(100, 100, 100))

        if max_catchable > 0:
            if gap > max_catchable:
                clinch_str = "WON"
                clinch_color = (0, 220, 80)
            elif gap > max_catchable * 0.75:
                clinch_str = "NEAR"
                clinch_color = (255, 180, 0)
            else:
                clinch_str = "ALIVE"
                clinch_color = (180, 180, 180)
            clinch_w = self._tw(draw, clinch_str, self.fonts["small"])
            draw.text((self.display_width - clinch_w - 4, gap_row_y),
                      clinch_str, font=self.fonts["small"], fill=clinch_color)

        # ── Closability gap bar ───────────────────────────────────
        bar_x0 = 3
        bar_x1 = self.display_width - 4
        bar_w = bar_x1 - bar_x0
        draw.rectangle([bar_x0, bar_y, bar_x1, bar_y + bar_h - 1], fill=(25, 25, 25))
        if max_catchable > 0:
            fill_ratio = min(1.0, gap / max_catchable)
            fill_w = int(bar_w * fill_ratio)
            if fill_w > 0:
                bar_fill = tuple(max(0, int(c * 0.7)) for c in p2_tc)
                draw.rectangle([bar_x0, bar_y, bar_x0 + fill_w, bar_y + bar_h - 1],
                               fill=bar_fill)
            draw.rectangle([bar_x0, bar_y, bar_x0 + 2, bar_y + bar_h - 1],
                           fill=tuple(max(0, int(c * 0.9)) for c in p1_tc))

        if is_live and live_session:
            self._draw_live_badge(draw, live_session)

        return img

    # ─── F1 Separator Card ────────────────────────────────────────────

    def render_f1_separator(self) -> Image.Image:
        """F1 logo separator card for vegas scroll transition."""
        img = Image.new("RGBA", (self.display_height, self.display_height), (0, 0, 0, 255))
        logo = self.logo_loader.get_f1_logo(
            max_height=int(self.display_height * 0.65),
            max_width=int(self.display_height * 0.65))
        if logo:
            x = (self.display_height - logo.width) // 2
            y = (self.display_height - logo.height) // 2
            img.paste(logo, (x, y), logo)
        return img
