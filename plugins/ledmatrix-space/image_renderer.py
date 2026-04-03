"""
Image Renderer for Space & Astronomy Tracker Plugin

Renders ISS countdowns, launch countdowns, planet icons, constellation line art,
and NASA APOD — all composited over a twinkling starfield background.
"""

import logging
import os
import random
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    try:
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (255, 255, 255)


class ImageRenderer:
    """Renders space-themed displays with starfield backgrounds."""

    FONT_PROFILES = {
        16: {'small': ('4x6-font.ttf', 5), 'name': ('4x6-font.ttf', 6), 'score': ('PressStart2P-Regular.ttf', 6), 'header': ('4x6-font.ttf', 5)},
        32: {'small': ('4x6-font.ttf', 6), 'name': ('4x6-font.ttf', 8), 'score': ('PressStart2P-Regular.ttf', 6), 'header': ('4x6-font.ttf', 6)},
        64: {'small': ('4x6-font.ttf', 8), 'name': ('4x6-font.ttf', 10), 'score': ('PressStart2P-Regular.ttf', 10), 'header': ('4x6-font.ttf', 8)},
    }

    def __init__(self, display_width: int, display_height: int,
                 colors: Optional[Dict[str, str]] = None,
                 starfield_config: Optional[Dict[str, Any]] = None,
                 logger: Optional[logging.Logger] = None):
        self.w = display_width
        self.h = display_height
        self.logger = logger or logging.getLogger(__name__)

        # Colors
        c = colors or {}
        self.color_countdown = _hex_to_rgb(c.get('countdown_color', '#00CCFF'))
        self.color_alert = _hex_to_rgb(c.get('alert_color', '#FFFFFF'))
        self.color_go = _hex_to_rgb(c.get('go_color', '#00FF00'))
        self.color_hold = _hex_to_rgb(c.get('hold_color', '#FF4400'))
        self.color_tbd = _hex_to_rgb(c.get('tbd_color', '#FFAA00'))
        self.color_header = _hex_to_rgb(c.get('header_color', '#4488FF'))
        self.color_planet_label = _hex_to_rgb(c.get('planet_label_color', '#AACCFF'))
        self.color_const_line = _hex_to_rgb(c.get('constellation_line_color', '#223366'))
        self.color_const_star = _hex_to_rgb(c.get('constellation_star_color', '#FFFFFF'))
        self.color_dim = (80, 80, 80)

        # Starfield
        sf = starfield_config or {}
        self.starfield_enabled = sf.get('enabled', True)
        self.star_density = sf.get('density', 30)
        self.twinkle_rate = sf.get('twinkle_rate', 3)
        self._starfield = self._generate_starfield()

        # Fonts
        self.fonts = self._load_fonts()

        # Assets
        self._plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self._planet_icons = self._load_planet_icons()
        self._iss_icon = self._load_iss_icon()

    # ── Setup ───────────────────────────────────────────────────

    def _load_fonts(self) -> Dict[str, ImageFont.FreeTypeFont]:
        fonts = {}
        font_dir = "assets/fonts"
        h = self.h
        profile = self.FONT_PROFILES[16] if h <= 16 else (self.FONT_PROFILES[32] if h <= 32 else self.FONT_PROFILES[64])

        for role, (font_file, size) in profile.items():
            for try_file in [font_file, "4x6-font.ttf", "PressStart2P-Regular.ttf"]:
                try:
                    fonts[role] = ImageFont.truetype(os.path.join(font_dir, try_file), size)
                    break
                except (IOError, OSError):
                    continue
            if role not in fonts:
                fonts[role] = ImageFont.load_default()
        return fonts

    def _load_planet_icons(self) -> Dict[str, Image.Image]:
        icons = {}
        planet_dir = os.path.join(self._plugin_dir, 'assets', 'planets')
        for name in ['mercury', 'venus', 'mars', 'jupiter', 'saturn']:
            path = os.path.join(planet_dir, f'{name}.png')
            try:
                icons[name] = Image.open(path).convert('RGBA')
            except Exception:
                pass
        return icons

    def _load_iss_icon(self) -> Optional[Image.Image]:
        path = os.path.join(self._plugin_dir, 'assets', 'iss_icon.png')
        try:
            return Image.open(path).convert('RGBA')
        except Exception:
            return None

    # ── Starfield ───────────────────────────────────────────────

    def _generate_starfield(self) -> List[Tuple[int, int, int]]:
        """Generate random star positions with brightness levels."""
        random.seed(42)  # Deterministic base pattern
        stars = []
        for _ in range(self.star_density):
            x = random.randint(0, self.w - 1)
            y = random.randint(0, self.h - 1)
            brightness = random.choice([40, 60, 80, 120, 160, 200])
            stars.append((x, y, brightness))
        return stars

    def _draw_starfield(self, img: Image.Image) -> None:
        """Draw twinkling starfield onto image."""
        if not self.starfield_enabled:
            return
        pixels = img.load()
        for i, (x, y, base_bright) in enumerate(self._starfield):
            # Twinkle: randomly vary brightness
            bright = base_bright
            if self.twinkle_rate > 0 and random.random() < self.twinkle_rate / len(self._starfield):
                bright = random.choice([30, 80, 160, 255])
                self._starfield[i] = (x, y, bright)

            # Only draw star if pixel is currently black (don't overwrite content)
            try:
                r, g, b = pixels[x, y][:3] if isinstance(pixels[x, y], tuple) and len(pixels[x, y]) >= 3 else (0, 0, 0)
                if r + g + b < 20:
                    pixels[x, y] = (bright, bright, bright)
            except (IndexError, TypeError):
                pass

    # ── Helpers ──────────────────────────────────────────────────

    def _tw(self, text: str, font) -> int:
        return font.getbbox(text)[2] - font.getbbox(text)[0]

    def _th(self, text: str, font) -> int:
        bbox = font.getbbox(text)
        return bbox[3] - bbox[1]

    def _center_x(self, text: str, font) -> int:
        return max(0, (self.w - self._tw(text, font)) // 2)

    def _outlined(self, draw, pos, text, font, fill, outline=(0, 0, 0)):
        x, y = pos
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
        draw.text(pos, text, font=font, fill=fill)

    def _new_image(self) -> Image.Image:
        """Create a new image with starfield background."""
        img = Image.new('RGB', (self.w, self.h), (0, 0, 0))
        self._draw_starfield(img)
        return img

    def _new_wide_image(self, width: int) -> Image.Image:
        """Create a wide scrolling image with starfield tiled."""
        img = Image.new('RGB', (width, self.h), (0, 0, 0))
        # Tile starfield across width
        if self.starfield_enabled:
            pixels = img.load()
            for sx, sy, bright in self._starfield:
                for offset in range(0, width, self.w):
                    x = sx + offset
                    if x < width:
                        pixels[x, sy] = (bright, bright, bright)
        return img

    # ── ISS Display ─────────────────────────────────────────────

    def render_iss(self, iss_data: Dict[str, Any]) -> Image.Image:
        """Render ISS pass countdown with satellite icon and position."""
        if iss_data.get('is_overhead'):
            return self._render_iss_alert(iss_data)

        img = self._new_image()
        draw = ImageDraw.Draw(img)
        font_name = self.fonts['name']
        font_score = self.fonts['score']
        font_small = self.fonts['small']
        font_header = self.fonts['header']

        # ISS icon in top-left
        if self._iss_icon:
            img.paste(self._iss_icon, (1, 1), self._iss_icon)

        # Header: "ISS" next to icon
        self._outlined(draw, (11, 1), "ISS", font_header, self.color_header)

        # Countdown
        next_pass = iss_data.get('next_pass')
        if next_pass and next_pass.get('start_time'):
            now = datetime.now(timezone.utc)
            delta = next_pass['start_time'] - now
            total_sec = max(0, int(delta.total_seconds()))

            if total_sec > 86400:
                days = total_sec // 86400
                hours = (total_sec % 86400) // 3600
                countdown = f"{days}d {hours}h"
            elif total_sec > 3600:
                hours = total_sec // 3600
                mins = (total_sec % 3600) // 60
                countdown = f"{hours}h {mins:02d}m"
            else:
                mins = total_sec // 60
                secs = total_sec % 60
                countdown = f"{mins}:{secs:02d}"

            # Big countdown centered
            cd_y = 10
            x = self._center_x(countdown, font_score)
            self._outlined(draw, (x, cd_y), countdown, font_score, self.color_countdown)

            # Direction and elevation
            direction = next_pass.get('direction', '')
            elevation = next_pass.get('max_elevation', 0)
            info = f"{direction} {elevation}deg"
            info_y = cd_y + self._th(countdown, font_score) + 3
            x2 = self._center_x(info, font_small)
            draw.text((x2, info_y), info, fill=self.color_dim, font=font_small)
        else:
            # No pass data
            text = "NO PASS DATA"
            x = self._center_x(text, font_small)
            draw.text((x, 14), text, fill=self.color_dim, font=font_small)

        # Position at bottom
        pos = iss_data.get('position', {})
        region = pos.get('region', '')
        if region:
            region_text = f"Over {region}"
            rw = self._tw(region_text, font_small)
            if rw > self.w - 4:
                region_text = region
            rx = self._center_x(region_text, font_small)
            draw.text((rx, self.h - 7), region_text, fill=self.color_dim, font=font_small)

        return img

    def _render_iss_alert(self, iss_data: Dict[str, Any]) -> Image.Image:
        """Render ISS OVERHEAD NOW alert with pulsing effect."""
        img = self._new_image()
        draw = ImageDraw.Draw(img)
        font_score = self.fonts['score']
        font_name = self.fonts['name']

        # Pulsing brightness based on time
        pulse = abs((datetime.now().microsecond // 100000) - 5) / 5.0  # 0.0-1.0
        r, g, b = self.color_alert
        pr = int(r * (0.5 + 0.5 * pulse))
        pg = int(g * (0.5 + 0.5 * pulse))
        pb = int(b * (0.5 + 0.5 * pulse))
        pulse_color = (pr, pg, pb)

        # "ISS" with icon
        if self._iss_icon:
            # Animate icon across display based on pass progress
            next_pass = iss_data.get('next_pass', {})
            progress = 0.5
            if next_pass.get('start_time') and next_pass.get('end_time'):
                now = datetime.now(timezone.utc)
                total = (next_pass['end_time'] - next_pass['start_time']).total_seconds()
                elapsed = (now - next_pass['start_time']).total_seconds()
                progress = min(1.0, max(0.0, elapsed / total)) if total > 0 else 0.5
            icon_x = int(progress * (self.w - 8))
            img.paste(self._iss_icon, (icon_x, 1), self._iss_icon)

        # "OVERHEAD!" text
        text1 = "ISS"
        text2 = "OVERHEAD!"
        y1 = 10
        y2 = y1 + self._th(text1, font_score) + 3
        self._outlined(draw, (self._center_x(text1, font_score), y1), text1, font_score, pulse_color)
        self._outlined(draw, (self._center_x(text2, font_name), y2), text2, font_name, pulse_color)

        return img

    # ── Launch Display ──────────────────────────────────────────

    def render_launch(self, launch_data: Dict[str, Any]) -> Image.Image:
        """Render next launch countdown with mission details."""
        img = self._new_image()
        draw = ImageDraw.Draw(img)
        font_score = self.fonts['score']
        font_name = self.fonts['name']
        font_small = self.fonts['small']
        font_header = self.fonts['header']

        # Status color
        status_id = launch_data.get('status_id', 2)
        if status_id == 1:
            status_color = self.color_go
        elif status_id in (4, 5, 7):
            status_color = self.color_hold
        else:
            status_color = self.color_tbd

        # Provider abbreviation in top-right
        abbr = launch_data.get('provider_abbr', '')
        if abbr:
            aw = self._tw(abbr, font_small)
            draw.text((self.w - aw - 1, 1), abbr, fill=self.color_dim, font=font_small)

        # "LAUNCH" header
        self._outlined(draw, (1, 1), "LAUNCH", font_header, self.color_header)

        # Countdown
        net = launch_data.get('net')
        if net:
            now = datetime.now(timezone.utc)
            delta = net - now
            total_sec = max(0, int(delta.total_seconds()))

            if total_sec > 86400:
                days = total_sec // 86400
                hours = (total_sec % 86400) // 3600
                mins = (total_sec % 3600) // 60
                countdown = f"T-{days}d {hours:02d}:{mins:02d}"
            elif total_sec > 3600:
                hours = total_sec // 3600
                mins = (total_sec % 3600) // 60
                secs = total_sec % 60
                countdown = f"T-{hours}:{mins:02d}:{secs:02d}"
            else:
                mins = total_sec // 60
                secs = total_sec % 60
                countdown = f"T-{mins}:{secs:02d}"
        else:
            countdown = "T- TBD"

        cd_y = 10
        x = self._center_x(countdown, font_score)
        self._outlined(draw, (x, cd_y), countdown, font_score, status_color)

        # Mission name at bottom (truncated)
        mission = launch_data.get('mission', launch_data.get('name', ''))
        max_chars = self.w // 5
        if len(mission) > max_chars:
            mission = mission[:max_chars-2] + ".."
        mx = self._center_x(mission, font_small)
        draw.text((mx, self.h - 7), mission, fill=self.color_dim, font=font_small)

        return img

    def render_launch_ticker(self, launch_data: Dict[str, Any]) -> Image.Image:
        """Render scrolling launch mission details."""
        font = self.fonts['name']
        font_small = self.fonts['small']

        provider = launch_data.get('provider', '')
        rocket = launch_data.get('rocket', '')
        mission = launch_data.get('mission', '')
        pad = launch_data.get('pad', '')
        status = launch_data.get('status', 'TBD')

        top_text = f"{provider} {rocket}"
        bot_text = f"{mission}  |  {pad}  |  {status}"

        top_w = self._tw(top_text, font)
        bot_w = self._tw(bot_text, font_small)
        total_w = max(top_w, bot_w, self.w + 1)

        img = self._new_wide_image(total_w)
        draw = ImageDraw.Draw(img)

        top_h = self._th("A", font)
        bot_h = self._th("A", font_small)
        total_text_h = top_h + 2 + bot_h
        y_top = max(0, (self.h - total_text_h) // 2)
        y_bot = y_top + top_h + 2

        self._outlined(draw, (0, y_top), top_text, font, self.color_countdown)
        draw.text((0, y_bot), bot_text, fill=self.color_dim, font=font_small)

        return img

    # ── Night Sky Display ───────────────────────────────────────

    def render_night_sky(self, planets: List[Dict[str, Any]],
                         constellation: Optional[Dict[str, Any]]) -> Image.Image:
        """Render visible planets with icons and a featured constellation."""
        img = self._new_image()
        draw = ImageDraw.Draw(img)
        font_small = self.fonts['small']
        font_header = self.fonts['header']

        # Header
        self._outlined(draw, (1, 1), "TONIGHT", font_header, self.color_header)

        if constellation:
            # Draw constellation on right half
            self._draw_constellation(img, draw, constellation, area=(self.w // 2, 0, self.w, self.h - 8))

            # Draw constellation name at bottom-right
            cname = constellation.get('display_name', '')
            cnw = self._tw(cname, font_small)
            draw.text((self.w - cnw - 1, self.h - 7), cname, fill=self.color_const_star, font=font_small)

        # Draw planet list on left side
        y = 9
        max_planets = min(len(planets), 3)  # Fit up to 3
        for i, planet in enumerate(planets[:max_planets]):
            name = planet.get('name', '?')
            rise = planet.get('rise_time', '')

            # Planet icon
            icon_key = name.lower()
            icon = self._planet_icons.get(icon_key)
            if icon:
                # Scale icon to fit (max 10px tall on 32px display)
                icon_size = min(10, self.h // 4)
                scaled = icon.resize((icon_size, icon_size), Image.NEAREST)
                img.paste(scaled, (1, y), scaled)
                text_x = icon_size + 3
            else:
                text_x = 1

            # Planet name and rise time
            draw.text((text_x, y), name[:3], fill=self.color_planet_label, font=font_small)
            if rise:
                rise_short = rise.replace(' PM', 'p').replace(' AM', 'a')
                draw.text((text_x, y + 7), rise_short, fill=self.color_dim, font=font_small)

            y += max(14, self.h // 4)

        return img

    def _draw_constellation(self, img: Image.Image, draw: ImageDraw.Draw,
                            constellation: Dict[str, Any],
                            area: Tuple[int, int, int, int]) -> None:
        """Draw constellation line art in the specified area."""
        x0, y0, x1, y1 = area
        aw = x1 - x0
        ah = y1 - y0
        margin = 3

        stars = constellation.get('stars', [])
        lines = constellation.get('lines', [])
        if not stars:
            return

        # Map normalized coordinates (0-100) to pixel area
        def to_px(sx, sy):
            px = x0 + margin + int(sx / 100.0 * (aw - margin * 2))
            py = y0 + margin + int(sy / 100.0 * (ah - margin * 2))
            return (px, py)

        star_positions = [to_px(s['x'], s['y']) for s in stars]

        # Draw lines first (behind stars)
        for a, b in lines:
            if a < len(star_positions) and b < len(star_positions):
                draw.line([star_positions[a], star_positions[b]], fill=self.color_const_line)

        # Draw stars
        for i, (px, py) in enumerate(star_positions):
            mag = stars[i].get('mag', 2.0) if i < len(stars) else 2.0
            # Brighter stars (lower mag) get bigger dots
            if mag < 1.0:
                draw.rectangle([px-1, py-1, px+1, py+1], fill=self.color_const_star)
            else:
                draw.point((px, py), fill=self.color_const_star)

    # ── APOD Display ────────────────────────────────────────────

    def render_apod(self, apod_data: Dict[str, Any]) -> Image.Image:
        """Render APOD title as scrolling text over starfield."""
        font = self.fonts['name']
        font_small = self.fonts['small']

        title = apod_data.get('title', 'Astronomy Picture of the Day')
        date = apod_data.get('date', '')

        top_text = f"APOD: {title}"
        bot_text = date

        top_w = self._tw(top_text, font)
        total_w = max(top_w, self.w + 1)

        img = self._new_wide_image(total_w)
        draw = ImageDraw.Draw(img)

        top_h = self._th("A", font)
        bot_h = self._th("A", font_small)
        total_text_h = top_h + 2 + bot_h
        y_top = max(0, (self.h - total_text_h) // 2)
        y_bot = y_top + top_h + 2

        self._outlined(draw, (0, y_top), top_text, font, self.color_countdown)
        draw.text((0, y_bot), bot_text, fill=self.color_dim, font=font_small)

        return img

    # ── Fallback ────────────────────────────────────────────────

    def render_no_data(self) -> Image.Image:
        """Render fallback with starfield and plugin name."""
        img = self._new_image()
        draw = ImageDraw.Draw(img)
        font_score = self.fonts['score']
        font_small = self.fonts['small']

        line1 = "SPACE"
        line2 = "SET LOCATION"
        h1 = self._th(line1, font_score)
        h2 = self._th(line2, font_small)
        gap = 3
        y = (self.h - h1 - gap - h2) // 2

        self._outlined(draw, (self._center_x(line1, font_score), y), line1, font_score, self.color_header)
        draw.text((self._center_x(line2, font_small), y + h1 + gap), line2, fill=self.color_dim, font=font_small)

        return img

    # ── People in Space ─────────────────────────────────────────

    def render_people_in_space(self, people: List[Dict[str, str]]) -> Image.Image:
        """Render scrolling ticker of people currently in space."""
        font = self.fonts['name']
        font_small = self.fonts['small']

        count = len(people)
        names = "  |  ".join(p.get('name', '?') for p in people)
        top_text = f"{count} IN SPACE"
        bot_text = names

        top_w = self._tw(top_text, font)
        bot_w = self._tw(bot_text, font_small)
        total_w = max(top_w, bot_w, self.w + 1)

        img = self._new_wide_image(total_w)
        draw = ImageDraw.Draw(img)

        top_h = self._th("A", font)
        bot_h = self._th("A", font_small)
        total_text_h = top_h + 2 + bot_h
        y_top = max(0, (self.h - total_text_h) // 2)
        y_bot = y_top + top_h + 2

        self._outlined(draw, (0, y_top), top_text, font, self.color_countdown)
        draw.text((0, y_bot), bot_text, fill=self.color_planet_label, font=font_small)

        return img
