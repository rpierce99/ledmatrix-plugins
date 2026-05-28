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
            tc = get_team_color(cid)
            dim_tc = tuple(max(80, int(c * 0.75)) for c in tc)
            self._draw_text_outlined(draw, (x, row2_y), team_disp, self.fonts["small"],
                                     fill=dim_tc, outline=(0, 0, 0))

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

            # Driver code on second row
            code_y = py2 + self._th(draw, pos_label, self.fonts["small"]) + 1
            code_max_w = section_w - 4 - (mini_logo.width + 2 if mini_logo else 0)
            code_trunc = self._truncate(draw, code, self.fonts["detail"], code_max_w)
            self._draw_text_outlined(draw, (x0 + 2, code_y), code_trunc,
                                     self.fonts["detail"], fill=(240, 240, 240))

            # Gap or time on third row
            gap_str = ""
            if i == 0:
                gap_str = r.get("time", "")
                gap_color = (160, 160, 160)
            else:
                gap_str = r.get("time", r.get("gap", ""))
                gap_color = (255, 190, 50)

            gap_y = code_y + self._th(draw, "A", self.fonts["detail"]) + 1
            if gap_str and gap_y + 5 < self.display_height:
                gap_trunc = self._truncate(draw, gap_str, self.fonts["small"], section_w - 4)
                draw.text((x0 + 2, gap_y), gap_trunc, font=self.fonts["small"], fill=gap_color)

            # Team color line at bottom
            draw.rectangle([x0 + 1, self.display_height - 2, x1 - 1, self.display_height - 1],
                           fill=tuple(max(0, int(c * 0.6)) for c in tc))

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
            is_live: bool = False, live_session: str = "") -> Image.Image:
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

        # ── Row 3: Wins · Poles + "MY DRIVER" badge ─────────────
        row3_y = row2_y + self._th(draw, "A", self.fonts["small"]) + 2 if row2_y + 5 < content_h else row2_y
        if row3_y + 5 < content_h:
            wins = driver_entry.get("wins", 0)
            poles = driver_entry.get("poles", 0)
            wp_text = f"{wins}W  {poles}P"
            draw.text((x, row3_y), wp_text, font=self.fonts["small"], fill=(130, 130, 130))

            label = "MY DRIVER"
            lw2 = self._tw(draw, label, self.fonts["small"]) + 4
            lx = self.display_width - lw2 - 1
            # Badge for "MY DRIVER"
            badge_col = tuple(max(0, int(c * 0.35)) for c in tc)
            draw.rectangle([lx, row3_y - 1, lx + lw2, row3_y + self._th(draw, label, self.fonts["small"]) + 1],
                           fill=badge_col)
            draw.text((lx + 2, row3_y), label, font=self.fonts["small"], fill=_team_color_bright(cid))

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
