"""
Image Renderer for Space & Astronomy Tracker Plugin

Renders ISS countdowns, launch countdowns, planet icons, constellation line art,
and NASA APOD — all composited over a twinkling starfield background.

Design principle: each screen shows 1-2 pieces of info BIG.
On a 32px display, that means 2 rows max — a bold headline and one supporting line.
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

    # Font sizing: big = primary info (numbers, alerts), med = labels, sm = secondary
    # Everything must be legible from 3+ feet away on an LED matrix
    FONT_PROFILES = {
        16: {'big': ('PressStart2P-Regular.ttf', 6),  'med': ('PressStart2P-Regular.ttf', 6), 'sm': ('4x6-font.ttf', 6)},
        32: {'big': ('PressStart2P-Regular.ttf', 10), 'med': ('PressStart2P-Regular.ttf', 8), 'sm': ('4x6-font.ttf', 10)},
        64: {'big': ('PressStart2P-Regular.ttf', 18), 'med': ('PressStart2P-Regular.ttf', 12), 'sm': ('4x6-font.ttf', 14)},
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
        self.c_dim = (90, 90, 110)

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
            for try_f in [fname, "PressStart2P-Regular.ttf", "4x6-font.ttf"]:
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

    def _load_asset(self, *parts) -> Optional[Image.Image]:
        try:
            return Image.open(os.path.join(self._plugin_dir, *parts)).convert('RGBA')
        except Exception:
            return None

    # ── Starfield ───────────────────────────────────────────────

    def _gen_starfield(self) -> List[List]:
        random.seed(42)
        return [[random.randint(0, max(self.w-1, 1)),
                 random.randint(0, max(self.h-1, 1)),
                 random.choice([25, 40, 60, 90, 130])]
                for _ in range(self.star_count)]

    def _apply_stars(self, img: Image.Image) -> None:
        """Apply starfield to black pixels only, with twinkle."""
        if not self.starfield_on:
            return
        px = img.load()
        for s in self._stars:
            x, y, b = s
            if self.twinkle_n > 0 and random.random() < self.twinkle_n * 2 / max(len(self._stars), 1):
                b = random.choice([15, 50, 110, 180])
                s[2] = b
            try:
                r, g, bl = px[x, y][:3]
                if r + g + bl < 10:
                    px[x, y] = (b, b, int(b * 1.15))
            except (IndexError, TypeError):
                pass

    def _bg(self, width: int = 0) -> Image.Image:
        w = width or self.w
        img = Image.new('RGB', (w, self.h), (0, 0, 0))
        return img

    # ── Text helpers ────────────────────────────────────────────

    def _tw(self, t, f):
        bb = f.getbbox(t); return bb[2] - bb[0]

    def _th(self, t, f):
        bb = f.getbbox(t); return bb[3] - bb[1]

    def _cx(self, t, f):
        return max(0, (self.w - self._tw(t, f)) // 2)

    def _out(self, d, pos, t, f, fill, outline=(0, 0, 0)):
        x, y = pos
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            d.text((x+dx, y+dy), t, font=f, fill=outline)
        d.text(pos, t, font=f, fill=fill)

    def _fit(self, text: str, font, max_w: int) -> str:
        if self._tw(text, font) <= max_w:
            return text
        while len(text) > 2 and self._tw(text + "..", font) > max_w:
            text = text[:-1]
        return text.rstrip() + ".."

    def _auto_font(self, text: str, preferred_font, fallback_font, max_w: int):
        """Use preferred font if it fits, otherwise fall back to smaller."""
        if self._tw(text, preferred_font) <= max_w:
            return preferred_font
        return fallback_font

    # ── ISS Countdown ───────────────────────────────────────────

    def render_iss(self, data: Dict[str, Any]) -> Image.Image:
        """
        ISS pass countdown. Two rows:
          Row 1: "ISS" label (med font, left) + direction info (right)
          Row 2: BIG countdown centered
        """
        if data.get('is_overhead'):
            return self._render_iss_alert(data)

        img = self._bg()
        d = ImageDraw.Draw(img)
        fb = self.fonts['big']
        fm = self.fonts['med']
        fs = self.fonts['sm']

        npass = data.get('next_pass')
        if npass and npass.get('start_time'):
            now = datetime.now(timezone.utc)
            secs = max(0, int((npass['start_time'] - now).total_seconds()))

            if secs > 86400:
                cd = f"{secs//86400}d {(secs%86400)//3600}h"
            elif secs > 3600:
                cd = f"{secs//3600}h{(secs%3600)//60:02d}m"
            else:
                cd = f"{secs//60}:{secs%60:02d}"

            # Pick font: use big if it fits, else med
            cd_font = self._auto_font(cd, fb, fm, self.w - 4)

            # Row 1: "ISS" left, direction right
            row1_y = 2
            if self._iss_icon:
                img.paste(self._iss_icon, (1, row1_y), self._iss_icon)
                self._out(d, (11, row1_y), "ISS", fm, self.c_header)
            else:
                self._out(d, (1, row1_y), "ISS", fm, self.c_header)

            direction = npass.get('direction', '')
            elev = npass.get('max_elevation', 0)
            if direction:
                info = f"{direction} {elev}\xb0"
                info = self._fit(info, fs, self.w // 2)
                iw = self._tw(info, fs)
                d.text((self.w - iw - 1, row1_y + 1), info, fill=self.c_dim, font=fs)

            # Row 2: BIG countdown centered in remaining space
            cd_h = self._th(cd, cd_font)
            row2_y = max(row1_y + self._th("I", fm) + 3, (self.h - cd_h) // 2 + 2)
            self._out(d, (self._cx(cd, cd_font), row2_y), cd, cd_font, self.c_countdown)
        else:
            text = "NO PASSES"
            self._out(d, (self._cx(text, fm), (self.h - self._th(text, fm)) // 2), text, fm, self.c_dim)

        self._apply_stars(img)
        return img

    def _render_iss_alert(self, data: Dict[str, Any]) -> Image.Image:
        """Full-screen pulsing ISS OVERHEAD alert."""
        img = self._bg()
        d = ImageDraw.Draw(img)
        fb = self.fonts['big']
        fm = self.fonts['med']

        pulse = abs((datetime.now().microsecond // 100000) - 5) / 5.0
        r, g, b = self.c_alert
        pc = (int(r * (0.3 + 0.7 * pulse)), int(g * (0.3 + 0.7 * pulse)), int(b * (0.3 + 0.7 * pulse)))

        # Animate icon
        if self._iss_icon:
            npass = data.get('next_pass', {})
            progress = 0.5
            if npass.get('start_time') and npass.get('end_time'):
                now = datetime.now(timezone.utc)
                total = (npass['end_time'] - npass['start_time']).total_seconds()
                elapsed = (now - npass['start_time']).total_seconds()
                progress = min(1.0, max(0.0, elapsed / total)) if total > 0 else 0.5
            img.paste(self._iss_icon, (int(progress * (self.w - 8)), 0), self._iss_icon)

        # "ISS" big, "LOOK UP!" medium — two rows centered
        t1 = "ISS"
        t2 = "LOOK UP!"
        t2_font = self._auto_font(t2, fm, self.fonts['sm'], self.w - 4)
        h1 = self._th(t1, fb)
        h2 = self._th(t2, t2_font)
        gap = 3
        total_h = h1 + gap + h2
        y = (self.h - total_h) // 2

        self._out(d, (self._cx(t1, fb), y), t1, fb, pc)
        self._out(d, (self._cx(t2, t2_font), y + h1 + gap), t2, t2_font, pc)

        self._apply_stars(img)
        return img

    # ── Launch Countdown ────────────────────────────────────────

    def render_launch(self, data: Dict[str, Any]) -> Image.Image:
        """
        Launch countdown. Two rows:
          Row 1: Status label (colored) + provider abbreviation
          Row 2: BIG countdown
        """
        img = self._bg()
        d = ImageDraw.Draw(img)
        fb = self.fonts['big']
        fm = self.fonts['med']
        fs = self.fonts['sm']

        sid = data.get('status_id', 2)
        sc = self.c_go if sid == 1 else (self.c_hold if sid in (4, 5, 7) else self.c_tbd)

        # Countdown
        net = data.get('net')
        if net:
            now = datetime.now(timezone.utc)
            secs = max(0, int((net - now).total_seconds()))
            if secs > 86400:
                cd = f"T-{secs//86400}d{(secs%86400)//3600:02d}h"
            elif secs > 3600:
                cd = f"T-{secs//3600}:{(secs%3600)//60:02d}:{secs%60:02d}"
            else:
                cd = f"T-{secs//60}:{secs%60:02d}"
        else:
            cd = "T- TBD"

        cd_font = self._auto_font(cd, fb, fm, self.w - 4)

        # Row 1: Status + provider
        row1_y = 2
        status = data.get('status', 'TBD')
        self._out(d, (1, row1_y), status, fm, sc)

        abbr = data.get('provider_abbr', '')
        if abbr:
            aw = self._tw(abbr, fs)
            d.text((self.w - aw - 1, row1_y + 1), abbr, fill=self.c_dim, font=fs)

        # Row 2: BIG countdown centered
        cd_h = self._th(cd, cd_font)
        row2_y = max(row1_y + self._th("G", fm) + 3, (self.h - cd_h) // 2 + 2)
        self._out(d, (self._cx(cd, cd_font), row2_y), cd, cd_font, sc)

        self._apply_stars(img)
        return img

    def render_launch_ticker(self, data: Dict[str, Any]) -> Image.Image:
        """Scrolling mission details: provider+rocket top, mission+pad bottom."""
        fm = self.fonts['med']
        fs = self.fonts['sm']

        top = f"{data.get('provider','')} {data.get('rocket','')}"
        bot = f"{data.get('mission','')}  \u00b7  {data.get('pad','')}"

        tw = max(self._tw(top, fm), self._tw(bot, fs), self.w + 1)
        img = self._bg(tw)
        d = ImageDraw.Draw(img)

        h1 = self._th("A", fm)
        h2 = self._th("A", fs)
        gap = 2
        y1 = (self.h - h1 - gap - h2) // 2
        y2 = y1 + h1 + gap

        self._out(d, (0, y1), top, fm, self.c_countdown)
        d.text((0, y2), bot, fill=self.c_dim, font=fs)

        self._apply_stars(img)
        return img

    # ── Night Sky ───────────────────────────────────────────────

    def render_night_sky(self, planets: List[Dict[str, Any]],
                         constellation: Optional[Dict[str, Any]]) -> Image.Image:
        """
        Night sky: scrolling horizontal ticker.
        Each planet gets: [16x16 icon] NAME risetime   then constellation at the end.
        This avoids cramming everything into 64px static — let it scroll.
        """
        fm = self.fonts['med']
        fs = self.fonts['sm']

        icon_size = min(16, self.h - 4)  # Scale icons to display height
        entry_gap = 16

        # Build entries: planet icon + text blocks
        entries = []
        for planet in planets:
            name = planet.get('name', '?')
            rise = planet.get('rise_time', '')
            rise_short = rise.replace(' PM', 'p').replace(' AM', 'a')
            text = f"{name} {rise_short}" if rise_short else name
            text_w = self._tw(text, fm)
            total_w = icon_size + 3 + text_w
            entries.append({
                'icon_key': name.lower(),
                'text': text,
                'text_w': text_w,
                'total_w': total_w,
            })

        # Constellation entry at the end
        const_w = 0
        if constellation and constellation.get('stars'):
            const_draw_w = max(30, self.h)  # Square area for constellation
            cname = constellation.get('display_name', '')
            cname_w = self._tw(cname, fs)
            const_w = const_draw_w + entry_gap

        total_width = sum(e['total_w'] + entry_gap for e in entries) + const_w
        total_width = max(total_width, self.w + 1)

        img = self._bg(total_width)
        d = ImageDraw.Draw(img)

        # Draw planet entries
        x = 0
        for entry in entries:
            icon = self._planet_icons.get(entry['icon_key'])
            icon_y = (self.h - icon_size) // 2
            if icon:
                scaled = icon.resize((icon_size, icon_size), Image.NEAREST)
                img.paste(scaled, (x, icon_y), scaled)

            # Text vertically centered
            text_y = (self.h - self._th(entry['text'], fm)) // 2
            self._out(d, (x + icon_size + 3, text_y), entry['text'], fm, self.c_planet)

            x += entry['total_w'] + entry_gap

        # Draw constellation at end
        if constellation and constellation.get('stars'):
            cx0 = x
            cy0 = 2
            cx1 = x + const_draw_w
            cy1 = self.h - 10
            self._draw_constellation(img, d, constellation, (cx0, cy0, cx1, cy1))

            # Name centered below
            cname = constellation.get('display_name', '')
            cnx = cx0 + (const_draw_w - self._tw(cname, fs)) // 2
            self._out(d, (max(cx0, cnx), self.h - 9), cname, fs, self.c_cstar)

        self._apply_stars(img)
        return img

    def _draw_constellation(self, img: Image.Image, d: ImageDraw.Draw,
                            const: Dict[str, Any], area: Tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = area
        aw, ah = x1 - x0, y1 - y0
        pad = 2

        stars = const.get('stars', [])
        lines = const.get('lines', [])
        if not stars:
            return

        pts = []
        for s in stars:
            px = x0 + pad + int(s['x'] / 100 * (aw - pad * 2))
            py = y0 + pad + int(s['y'] / 100 * (ah - pad * 2))
            pts.append((px, py))

        for a, b in lines:
            if a < len(pts) and b < len(pts):
                d.line([pts[a], pts[b]], fill=self.c_cline, width=1)

        for i, (px, py) in enumerate(pts):
            mag = stars[i].get('mag', 2.0) if i < len(stars) else 2.0
            if mag < 0.5:
                d.rectangle([px-1, py-1, px+1, py+1], fill=self.c_cstar)
                for gx, gy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
                    try:
                        d.point((px+gx, py+gy), fill=self.c_cline)
                    except Exception:
                        pass
            elif mag < 1.5:
                d.rectangle([px, py, px+1, py+1], fill=self.c_cstar)
            else:
                d.point((px, py), fill=self.c_cstar)

    # ── People in Space ─────────────────────────────────────────

    def render_people_in_space(self, people: List[Dict[str, str]]) -> Image.Image:
        """Scrolling: big count + names."""
        fm = self.fonts['med']
        fs = self.fonts['sm']

        count = len(people)
        names = "  \u00b7  ".join(p.get('name', '?') for p in people)
        top = f"{count} IN SPACE"
        bot = names

        tw = max(self._tw(top, fm), self._tw(bot, fs), self.w + 1)
        img = self._bg(tw)
        d = ImageDraw.Draw(img)

        h1, h2 = self._th("A", fm), self._th("A", fs)
        gap = 2
        y1 = (self.h - h1 - gap - h2) // 2
        y2 = y1 + h1 + gap

        if self._iss_icon:
            img.paste(self._iss_icon, (0, y1), self._iss_icon)
            self._out(d, (10, y1), top, fm, self.c_countdown)
        else:
            self._out(d, (0, y1), top, fm, self.c_countdown)

        d.text((0, y2), bot, fill=self.c_planet, font=fs)

        self._apply_stars(img)
        return img

    # ── APOD ────────────────────────────────────────────────────

    def render_apod(self, data: Dict[str, Any]) -> Image.Image:
        """APOD title scrolling."""
        fm = self.fonts['med']
        fs = self.fonts['sm']

        title = data.get('title', 'Astronomy Picture of the Day')
        date = data.get('date', '')

        top = title
        bot = f"NASA APOD  \u00b7  {date}" if date else "NASA APOD"

        tw = max(self._tw(top, fm), self._tw(bot, fs), self.w + 1)
        img = self._bg(tw)
        d = ImageDraw.Draw(img)

        h1, h2 = self._th("A", fm), self._th("A", fs)
        gap = 2
        y1 = (self.h - h1 - gap - h2) // 2
        y2 = y1 + h1 + gap

        self._out(d, (0, y1), top, fm, self.c_countdown)
        d.text((0, y2), bot, fill=self.c_dim, font=fs)

        self._apply_stars(img)
        return img

    # ── Fallback ────────────────────────────────────────────────

    def render_no_data(self) -> Image.Image:
        img = self._bg()
        d = ImageDraw.Draw(img)
        fb = self.fonts['big']
        fs = self.fonts['sm']

        t1 = "SPACE"
        t2 = "SET LOCATION"
        t1_font = self._auto_font(t1, fb, self.fonts['med'], self.w - 4)
        t2_font = self._auto_font(t2, fs, self.fonts['sm'], self.w - 4)

        h1 = self._th(t1, t1_font)
        h2 = self._th(t2, t2_font)
        gap = 3
        y = (self.h - h1 - gap - h2) // 2

        self._out(d, (self._cx(t1, t1_font), y), t1, t1_font, self.c_header)
        d.text((self._cx(t2, t2_font), y + h1 + gap), t2, fill=self.c_dim, font=t2_font)

        self._apply_stars(img)
        return img
