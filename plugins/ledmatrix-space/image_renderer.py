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
    hex_color = hex_color.lstrip('#')
    try:
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (255, 255, 255)


class ImageRenderer:
    """Renders space-themed displays with starfield backgrounds."""

    # Font roles: countdown (bold digits), body (readable text), detail (small info)
    FONT_PROFILES = {
        16: {'countdown': ('PressStart2P-Regular.ttf', 5), 'body': ('4x6-font.ttf', 5), 'detail': ('4x6-font.ttf', 5)},
        32: {'countdown': ('PressStart2P-Regular.ttf', 6), 'body': ('4x6-font.ttf', 6), 'detail': ('4x6-font.ttf', 6)},
        64: {'countdown': ('PressStart2P-Regular.ttf', 10), 'body': ('4x6-font.ttf', 8), 'detail': ('4x6-font.ttf', 6)},
    }

    def __init__(self, display_width: int, display_height: int,
                 colors: Optional[Dict[str, str]] = None,
                 starfield_config: Optional[Dict[str, Any]] = None,
                 logger: Optional[logging.Logger] = None):
        self.w = display_width
        self.h = display_height
        self.logger = logger or logging.getLogger(__name__)

        c = colors or {}
        self.c_countdown = _hex_to_rgb(c.get('countdown_color', '#00CCFF'))
        self.c_alert = _hex_to_rgb(c.get('alert_color', '#FFFFFF'))
        self.c_go = _hex_to_rgb(c.get('go_color', '#00FF00'))
        self.c_hold = _hex_to_rgb(c.get('hold_color', '#FF4400'))
        self.c_tbd = _hex_to_rgb(c.get('tbd_color', '#FFAA00'))
        self.c_header = _hex_to_rgb(c.get('header_color', '#4488FF'))
        self.c_planet = _hex_to_rgb(c.get('planet_label_color', '#AACCFF'))
        self.c_cline = _hex_to_rgb(c.get('constellation_line_color', '#223366'))
        self.c_cstar = _hex_to_rgb(c.get('constellation_star_color', '#FFFFFF'))
        self.c_dim = (80, 80, 100)
        self.c_dimmer = (50, 50, 65)

        sf = starfield_config or {}
        self.starfield_on = sf.get('enabled', True)
        self.star_count = sf.get('density', 30)
        self.twinkle_n = sf.get('twinkle_rate', 3)
        self._stars = self._gen_starfield()

        self.fonts = self._load_fonts()

        self._plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self._planet_icons = self._load_planet_icons()
        self._iss_icon = self._load_asset('assets', 'iss_icon.png')

    # ── Setup ───────────────────────────────────────────────────

    def _load_fonts(self) -> Dict[str, ImageFont.FreeTypeFont]:
        fonts = {}
        profile = self.FONT_PROFILES.get(16 if self.h <= 16 else (32 if self.h <= 32 else 64))
        for role, (fname, size) in profile.items():
            for try_f in [fname, "4x6-font.ttf", "PressStart2P-Regular.ttf"]:
                try:
                    fonts[role] = ImageFont.truetype(os.path.join("assets/fonts", try_f), size)
                    break
                except (IOError, OSError):
                    continue
            if role not in fonts:
                fonts[role] = ImageFont.load_default()
        return fonts

    def _load_planet_icons(self) -> Dict[str, Image.Image]:
        icons = {}
        for name in ['mercury', 'venus', 'mars', 'jupiter', 'saturn']:
            img = self._load_asset('assets', 'planets', f'{name}.png')
            if img:
                icons[name] = img
        return icons

    def _load_asset(self, *path_parts) -> Optional[Image.Image]:
        path = os.path.join(self._plugin_dir, *path_parts)
        try:
            return Image.open(path).convert('RGBA')
        except Exception:
            return None

    # ── Starfield ───────────────────────────────────────────────

    def _gen_starfield(self) -> List[List]:
        random.seed(42)
        return [[random.randint(0, max(self.w - 1, 1)),
                 random.randint(0, max(self.h - 1, 1)),
                 random.choice([30, 50, 70, 100, 140, 200])]
                for _ in range(self.star_count)]

    def _apply_starfield(self, img: Image.Image) -> None:
        if not self.starfield_on:
            return
        px = img.load()
        for s in self._stars:
            x, y, b = s
            if self.twinkle_n > 0 and random.random() < self.twinkle_n * 2 / max(len(self._stars), 1):
                b = random.choice([20, 60, 130, 220])
                s[2] = b
            try:
                r, g, bl = px[x, y][:3]
                if r + g + bl < 15:
                    px[x, y] = (b, b, int(b * 1.1))  # Slightly blue-tinted stars
            except (IndexError, TypeError):
                pass

    def _bg(self, width: int = 0) -> Image.Image:
        """New image with starfield. If width > display, tiles stars."""
        w = width or self.w
        img = Image.new('RGB', (w, self.h), (0, 0, 0))
        if self.starfield_on:
            px = img.load()
            for s in self._stars:
                x, y, b = s
                for off in range(0, w, self.w):
                    xx = x + off
                    if xx < w:
                        px[xx, y] = (b, b, int(b * 1.1))
        return img

    # ── Text helpers ────────────────────────────────────────────

    def _tw(self, t, f):
        bb = f.getbbox(t); return bb[2] - bb[0]

    def _th(self, t, f):
        bb = f.getbbox(t); return bb[3] - bb[1]

    def _cx(self, t, f):
        return max(0, (self.w - self._tw(t, f)) // 2)

    def _rx(self, t, f, margin=1):
        return max(0, self.w - self._tw(t, f) - margin)

    def _out(self, d, pos, t, f, fill, outline=(0, 0, 0)):
        x, y = pos
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            d.text((x+dx, y+dy), t, font=f, fill=outline)
        d.text(pos, t, font=f, fill=fill)

    def _fit_text(self, text: str, font, max_w: int) -> str:
        """Truncate text to fit within max_w pixels."""
        if self._tw(text, font) <= max_w:
            return text
        while len(text) > 2 and self._tw(text + "..", font) > max_w:
            text = text[:-1]
        return text.rstrip() + ".."

    # ── ISS Countdown ───────────────────────────────────────────

    def render_iss(self, data: Dict[str, Any]) -> Image.Image:
        """ISS pass countdown with icon, direction, and position."""
        if data.get('is_overhead'):
            return self._render_iss_alert(data)

        img = self._bg()
        d = ImageDraw.Draw(img)
        fc = self.fonts['countdown']
        fb = self.fonts['body']
        fd = self.fonts['detail']

        # Row 1 (y=0): ISS icon + "ISS PASS" header
        if self._iss_icon:
            img.paste(self._iss_icon, (1, 1), self._iss_icon)
        self._out(d, (11, 1), "ISS PASS", fd, self.c_header)

        # Row 2 (y=9): Big countdown centered
        npass = data.get('next_pass')
        if npass and npass.get('start_time'):
            now = datetime.now(timezone.utc)
            secs = max(0, int((npass['start_time'] - now).total_seconds()))

            if secs > 86400:
                cd = f"{secs // 86400}d {(secs % 86400) // 3600}h"
            elif secs > 3600:
                cd = f"{secs // 3600}h {(secs % 3600) // 60:02d}m"
            else:
                cd = f"{secs // 60}:{secs % 60:02d}"

            cd_y = 9
            self._out(d, (self._cx(cd, fc), cd_y), cd, fc, self.c_countdown)

            # Row 3 (y=18): Direction + elevation
            direction = npass.get('direction', '')
            elev = npass.get('max_elevation', 0)
            info = f"{direction} {elev}°"
            info = self._fit_text(info, fd, self.w - 4)
            self._out(d, (self._cx(info, fd), 18), info, fd, self.c_dim)
        else:
            self._out(d, (self._cx("NO PASSES", fb), 12), "NO PASSES", fb, self.c_dim)

        # Row 4 (y=25): Position region
        region = data.get('position', {}).get('region', '')
        if region:
            loc = self._fit_text(f"Over {region}", fd, self.w - 2)
            d.text((self._cx(loc, fd), 25), loc, fill=self.c_dimmer, font=fd)

        self._apply_starfield(img)
        return img

    def _render_iss_alert(self, data: Dict[str, Any]) -> Image.Image:
        """Pulsing ISS OVERHEAD alert."""
        img = self._bg()
        d = ImageDraw.Draw(img)
        fc = self.fonts['countdown']
        fb = self.fonts['body']

        # Pulse brightness
        pulse = abs((datetime.now().microsecond // 100000) - 5) / 5.0
        r, g, b = self.c_alert
        pc = (int(r * (0.4 + 0.6 * pulse)), int(g * (0.4 + 0.6 * pulse)), int(b * (0.4 + 0.6 * pulse)))

        # Animate ISS icon across display
        if self._iss_icon:
            npass = data.get('next_pass', {})
            progress = 0.5
            if npass.get('start_time') and npass.get('end_time'):
                now = datetime.now(timezone.utc)
                total = (npass['end_time'] - npass['start_time']).total_seconds()
                elapsed = (now - npass['start_time']).total_seconds()
                progress = min(1.0, max(0.0, elapsed / total)) if total > 0 else 0.5
            ix = int(progress * (self.w - 8))
            img.paste(self._iss_icon, (ix, 0), self._iss_icon)

        # Two lines centered
        t1 = "ISS"
        t2 = "OVERHEAD!"
        h1 = self._th(t1, fc)
        h2 = self._th(t2, fb)
        gap = 2
        total = h1 + gap + h2
        y = (self.h - total) // 2

        self._out(d, (self._cx(t1, fc), y), t1, fc, pc)
        self._out(d, (self._cx(t2, fb), y + h1 + gap), t2, fb, pc)

        self._apply_starfield(img)
        return img

    # ── Launch Countdown ────────────────────────────────────────

    def render_launch(self, data: Dict[str, Any]) -> Image.Image:
        """Launch countdown with status color and mission info."""
        img = self._bg()
        d = ImageDraw.Draw(img)
        fc = self.fonts['countdown']
        fb = self.fonts['body']
        fd = self.fonts['detail']

        # Status color
        sid = data.get('status_id', 2)
        sc = self.c_go if sid == 1 else (self.c_hold if sid in (4, 5, 7) else self.c_tbd)

        # Row 1: "LAUNCH" left, provider abbreviation right
        self._out(d, (1, 1), "LAUNCH", fd, self.c_header)
        abbr = data.get('provider_abbr', '')
        if abbr:
            d.text((self._rx(abbr, fd), 1), abbr, fill=self.c_dim, font=fd)

        # Row 2: Big countdown
        net = data.get('net')
        if net:
            now = datetime.now(timezone.utc)
            secs = max(0, int((net - now).total_seconds()))
            if secs > 86400:
                dd = secs // 86400
                hh = (secs % 86400) // 3600
                mm = (secs % 3600) // 60
                cd = f"T-{dd}d {hh:02d}:{mm:02d}"
            elif secs > 3600:
                cd = f"T-{secs//3600}:{(secs%3600)//60:02d}:{secs%60:02d}"
            else:
                cd = f"T-{secs//60}:{secs%60:02d}"
        else:
            cd = "T- TBD"

        # If countdown is too wide, use body font
        cd_font = fc
        if self._tw(cd, fc) > self.w - 4:
            cd_font = fb

        cd_y = 9
        self._out(d, (self._cx(cd, cd_font), cd_y), cd, cd_font, sc)

        # Row 3: Status text
        status = data.get('status', 'TBD')
        d.text((self._cx(status, fd), 18), status, fill=sc, font=fd)

        # Row 4: Mission name (truncated)
        mission = data.get('mission', data.get('name', ''))
        mission = self._fit_text(mission, fd, self.w - 4)
        d.text((self._cx(mission, fd), 25), mission, fill=self.c_dimmer, font=fd)

        self._apply_starfield(img)
        return img

    def render_launch_ticker(self, data: Dict[str, Any]) -> Image.Image:
        """Scrolling launch mission details (two-row ticker)."""
        fb = self.fonts['body']
        fd = self.fonts['detail']

        provider = data.get('provider', '')
        rocket = data.get('rocket', '')
        mission = data.get('mission', '')
        pad = data.get('pad', '')

        top = f"{provider} {rocket}"
        bot = f"{mission}  ·  {pad}"

        tw = max(self._tw(top, fb), self._tw(bot, fd), self.w + 1)
        img = self._bg(tw)
        d = ImageDraw.Draw(img)

        th_top = self._th("A", fb)
        th_bot = self._th("A", fd)
        total = th_top + 2 + th_bot
        y1 = (self.h - total) // 2
        y2 = y1 + th_top + 2

        self._out(d, (0, y1), top, fb, self.c_countdown)
        d.text((0, y2), bot, fill=self.c_dim, font=fd)

        return img

    # ── Night Sky ───────────────────────────────────────────────

    def render_night_sky(self, planets: List[Dict[str, Any]],
                         constellation: Optional[Dict[str, Any]]) -> Image.Image:
        """Planets with icons on left, constellation on right."""
        img = self._bg()
        d = ImageDraw.Draw(img)
        fb = self.fonts['body']
        fd = self.fonts['detail']

        # Decide layout split: planets get left portion, constellation gets right
        has_const = constellation and constellation.get('stars')
        split_x = self.w // 2 if has_const else self.w

        # Header
        self._out(d, (1, 0), "TONIGHT", fd, self.c_header)

        # ── Planets (left side) ──
        icon_size = 8  # Scale planet icons to 8x8 for better fit
        y = 8
        max_planets = min(len(planets), 3)
        row_h = max(8, (self.h - 8) // max(max_planets, 1))

        for planet in planets[:max_planets]:
            name = planet.get('name', '?')
            rise = planet.get('rise_time', '')

            # Draw planet icon
            icon = self._planet_icons.get(name.lower())
            ix = 1
            if icon:
                scaled = icon.resize((icon_size, icon_size), Image.NEAREST)
                img.paste(scaled, (ix, y), scaled)
            tx = ix + icon_size + 2

            # Planet name
            short_name = name[:6]  # "Saturn" fits, "Jupiter" → "Jupite"
            self._out(d, (tx, y), short_name, fd, self.c_planet)

            # Rise time below name
            if rise:
                rise_short = rise.replace(' PM', 'p').replace(' AM', 'a')
                d.text((tx, y + 7), rise_short, fill=self.c_dimmer, font=fd)

            y += row_h

        # ── Constellation (right side) ──
        if has_const:
            # Draw area: right half with some padding
            cx0 = split_x + 2
            cy0 = 8
            cx1 = self.w - 1
            cy1 = self.h - 8
            self._draw_constellation(img, d, constellation, (cx0, cy0, cx1, cy1))

            # Constellation name centered under the drawing
            cname = constellation.get('display_name', '')
            cnw = self._tw(cname, fd)
            cnx = cx0 + ((cx1 - cx0) - cnw) // 2
            d.text((max(cx0, cnx), self.h - 7), cname, fill=self.c_cstar, font=fd)

        self._apply_starfield(img)
        return img

    def _draw_constellation(self, img: Image.Image, d: ImageDraw.Draw,
                            const: Dict[str, Any], area: Tuple[int, int, int, int]) -> None:
        """Draw constellation as dots + lines in the specified area."""
        x0, y0, x1, y1 = area
        aw, ah = x1 - x0, y1 - y0
        pad = 2

        stars = const.get('stars', [])
        lines = const.get('lines', [])
        if not stars:
            return

        # Map 0-100 coords to pixel area
        pts = []
        for s in stars:
            px = x0 + pad + int(s['x'] / 100 * (aw - pad * 2))
            py = y0 + pad + int(s['y'] / 100 * (ah - pad * 2))
            pts.append((px, py))

        # Draw connecting lines (dim blue)
        for a, b in lines:
            if a < len(pts) and b < len(pts):
                d.line([pts[a], pts[b]], fill=self.c_cline, width=1)

        # Draw stars (brighter = larger)
        for i, (px, py) in enumerate(pts):
            mag = stars[i].get('mag', 2.0) if i < len(stars) else 2.0
            if mag < 0.5:
                # Very bright: 3x3 with glow
                d.rectangle([px-1, py-1, px+1, py+1], fill=self.c_cstar)
                for gx, gy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
                    try:
                        d.point((px+gx, py+gy), fill=self.c_cline)
                    except Exception:
                        pass
            elif mag < 1.5:
                # Bright: 2x2
                d.rectangle([px, py, px+1, py+1], fill=self.c_cstar)
            else:
                # Dim: single pixel
                d.point((px, py), fill=self.c_cstar)

    # ── People in Space ─────────────────────────────────────────

    def render_people_in_space(self, people: List[Dict[str, str]]) -> Image.Image:
        """Scrolling ticker: count + crew names."""
        fb = self.fonts['body']
        fd = self.fonts['detail']

        count = len(people)
        names = "  ·  ".join(p.get('name', '?') for p in people)
        top = f"{count} IN SPACE"
        bot = names

        tw = max(self._tw(top, fb), self._tw(bot, fd), self.w + 1)
        img = self._bg(tw)
        d = ImageDraw.Draw(img)

        th1 = self._th("A", fb)
        th2 = self._th("A", fd)
        total = th1 + 2 + th2
        y1 = (self.h - total) // 2
        y2 = y1 + th1 + 2

        # ISS icon before count text
        if self._iss_icon:
            img.paste(self._iss_icon, (0, y1), self._iss_icon)
            self._out(d, (10, y1), top, fb, self.c_countdown)
        else:
            self._out(d, (0, y1), top, fb, self.c_countdown)

        d.text((0, y2), bot, fill=self.c_planet, font=fd)

        return img

    # ── APOD ────────────────────────────────────────────────────

    def render_apod(self, data: Dict[str, Any]) -> Image.Image:
        """APOD title as two-row scrolling ticker."""
        fb = self.fonts['body']
        fd = self.fonts['detail']

        title = data.get('title', 'Astronomy Picture of the Day')
        date = data.get('date', '')

        top = f"APOD: {title}"
        bot = date

        tw = max(self._tw(top, fb), self.w + 1)
        img = self._bg(tw)
        d = ImageDraw.Draw(img)

        th1 = self._th("A", fb)
        th2 = self._th("A", fd)
        total = th1 + 2 + th2
        y1 = (self.h - total) // 2
        y2 = y1 + th1 + 2

        self._out(d, (0, y1), top, fb, self.c_countdown)
        d.text((0, y2), bot, fill=self.c_dim, font=fd)

        return img

    # ── Fallback ────────────────────────────────────────────────

    def render_no_data(self) -> Image.Image:
        img = self._bg()
        d = ImageDraw.Draw(img)
        fc = self.fonts['countdown']
        fd = self.fonts['detail']

        t1 = "SPACE"
        t2 = "SET LOCATION"
        h1 = self._th(t1, fc)
        h2 = self._th(t2, fd)
        y = (self.h - h1 - 3 - h2) // 2

        self._out(d, (self._cx(t1, fc), y), t1, fc, self.c_header)
        d.text((self._cx(t2, fd), y + h1 + 3), t2, fill=self.c_dim, font=fd)

        self._apply_starfield(img)
        return img
