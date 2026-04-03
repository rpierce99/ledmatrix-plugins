"""
Image Renderer for Space & Astronomy Tracker Plugin

Design philosophy: NES-era HUD meets space mission control.
- Colored header bars with accent lines (like Masters plugin)
- Big readable countdown digits that fill the display
- 16x16 pixel-art planet sprites in scrolling tickers
- Constellation line art with star brightness tiers
- Twinkling starfield background on all screens
- Max 2 content rows on static screens for readability
"""

import logging
import os
import random
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont


def _hex(c: str) -> Tuple[int, int, int]:
    c = c.lstrip('#')
    try:
        return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (255, 255, 255)


class ImageRenderer:
    """Renders space-themed displays for LED matrix."""

    # Space color palette
    SPACE_BLUE = (8, 12, 28)        # Deep space background tint
    HEADER_BG = (12, 30, 60)        # Header bar background
    ACCENT_LINE = (0, 180, 255)     # Cyan accent line under headers
    ROW_ALT = (10, 18, 35)          # Alternating row tint

    FONT_PROFILES = {
        16: {'big': ('PressStart2P-Regular.ttf', 6),  'med': ('PressStart2P-Regular.ttf', 6),  'sm': ('4x6-font.ttf', 6)},
        32: {'big': ('PressStart2P-Regular.ttf', 10), 'med': ('PressStart2P-Regular.ttf', 8),  'sm': ('4x6-font.ttf', 10)},
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
        self.c_countdown = _hex(c.get('countdown_color', '#00CCFF'))
        self.c_alert = _hex(c.get('alert_color', '#FFFFFF'))
        self.c_go = _hex(c.get('go_color', '#00FF00'))
        self.c_hold = _hex(c.get('hold_color', '#FF4400'))
        self.c_tbd = _hex(c.get('tbd_color', '#FFAA00'))
        self.c_header = _hex(c.get('header_color', '#4488FF'))
        self.c_planet = _hex(c.get('planet_label_color', '#AACCFF'))
        self.c_cline = _hex(c.get('constellation_line_color', '#334477'))
        self.c_cstar = _hex(c.get('constellation_star_color', '#FFFFFF'))
        self.c_dim = (90, 95, 120)

        # Header bar height (NES-style top bar)
        self.hdr_h = max(9, self.h // 3)

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

    def _load_fonts(self):
        fonts = {}
        p = self.FONT_PROFILES.get(16 if self.h <= 16 else (32 if self.h <= 32 else 64))
        for role, (fn, sz) in p.items():
            for f in [fn, "PressStart2P-Regular.ttf", "4x6-font.ttf"]:
                try:
                    fonts[role] = ImageFont.truetype(os.path.join("assets/fonts", f), sz)
                    break
                except (IOError, OSError):
                    continue
            if role not in fonts:
                fonts[role] = ImageFont.load_default()
        return fonts

    def _load_planet_icons(self):
        icons = {}
        for n in ['mercury', 'venus', 'mars', 'jupiter', 'saturn']:
            i = self._load_asset('assets', 'planets', f'{n}.png')
            if i:
                icons[n] = i
        return icons

    def _load_asset(self, *parts):
        try:
            return Image.open(os.path.join(self._plugin_dir, *parts)).convert('RGBA')
        except Exception:
            return None

    # ── Starfield ───────────────────────────────────────────────

    def _gen_starfield(self):
        random.seed(42)
        return [[random.randint(0, max(self.w-1, 1)),
                 random.randint(0, max(self.h-1, 1)),
                 random.choice([20, 35, 55, 80, 120])]
                for _ in range(self.star_count)]

    def _apply_stars(self, img, y_start=0):
        """Apply stars only to black pixels below y_start (skip header area)."""
        if not self.starfield_on:
            return
        px = img.load()
        w = img.width
        for s in self._stars:
            x, y, b = s
            # Tile stars for wide images
            for ox in range(0, w, self.w):
                xx = x + ox
                if xx >= w:
                    break
                if y < y_start:
                    continue
                if self.twinkle_n > 0 and random.random() < self.twinkle_n * 2 / max(len(self._stars), 1):
                    b = random.choice([12, 40, 90, 160])
                    s[2] = b
                try:
                    r, g, bl = px[xx, y][:3]
                    if r + g + bl < 10:
                        px[xx, y] = (b, b, int(b * 1.1))
                except (IndexError, TypeError):
                    pass

    # ── Drawing helpers ─────────────────────────────────────────

    def _tw(self, t, f):
        bb = f.getbbox(t); return bb[2] - bb[0]
    def _th(self, t, f):
        bb = f.getbbox(t); return bb[3] - bb[1]
    def _cx(self, t, f, w=0):
        return max(0, ((w or self.w) - self._tw(t, f)) // 2)

    def _out(self, d, pos, t, f, fill, shadow=(0, 0, 0)):
        """Draw text with 1px drop shadow for readability."""
        x, y = pos
        d.text((x+1, y+1), t, font=f, fill=shadow)
        d.text(pos, t, font=f, fill=fill)

    def _fit(self, text, font, max_w):
        if self._tw(text, font) <= max_w:
            return text
        while len(text) > 2 and self._tw(text + "..", font) > max_w:
            text = text[:-1]
        return text.rstrip() + ".."

    def _auto_font(self, text, pref, fallback, max_w):
        return pref if self._tw(text, pref) <= max_w else fallback

    def _draw_header_bar(self, d, label, right_text="", label_color=None):
        """NES-style header bar: colored background + accent line + label."""
        d.rectangle([(0, 0), (self.w - 1, self.hdr_h - 2)], fill=self.HEADER_BG)
        d.line([(0, self.hdr_h - 1), (self.w, self.hdr_h - 1)], fill=self.ACCENT_LINE)

        fm = self.fonts['med']
        fs = self.fonts['sm']
        lc = label_color or self.c_header

        # Label left-aligned, vertically centered in header
        ly = (self.hdr_h - 1 - self._th(label, fm)) // 2
        self._out(d, (2, max(0, ly)), label, fm, lc)

        # Right text (dim)
        if right_text:
            rw = self._tw(right_text, fs)
            ry = (self.hdr_h - 1 - self._th(right_text, fs)) // 2
            d.text((self.w - rw - 2, max(0, ry)), right_text, fill=self.c_dim, font=fs)

    def _content_y(self):
        """Y position where content starts (below header bar)."""
        return self.hdr_h + 1

    # ── ISS Countdown ───────────────────────────────────────────

    def render_iss(self, data: Dict[str, Any]) -> Image.Image:
        """
        Header bar: [ISS icon] ISS PASS  |  SW 62°
        Content: BIG countdown centered in remaining space
        """
        if data.get('is_overhead'):
            return self._render_iss_alert(data)

        img = Image.new('RGB', (self.w, self.h), (0, 0, 0))
        d = ImageDraw.Draw(img)

        # Header bar
        npass = data.get('next_pass', {})
        direction = npass.get('direction', '')
        elev = npass.get('max_elevation', 0)
        right = f"{direction} {elev}\xb0" if direction else ""
        self._draw_header_bar(d, "ISS", right)

        # Paste ISS icon into header
        if self._iss_icon:
            iy = (self.hdr_h - 8) // 2
            # Shift label to make room for icon
            img.paste(self._iss_icon, (2, max(0, iy)), self._iss_icon)
            fm = self.fonts['med']
            ly = (self.hdr_h - 1 - self._th("ISS", fm)) // 2
            self._out(d, (12, max(0, ly)), "ISS", fm, self.c_header)

        # Content area: BIG countdown
        fb = self.fonts['big']
        fm = self.fonts['med']
        cy = self._content_y()
        content_h = self.h - cy

        if npass.get('start_time'):
            now = datetime.now(timezone.utc)
            secs = max(0, int((npass['start_time'] - now).total_seconds()))

            if secs > 86400:
                cd = f"{secs//86400}d {(secs%86400)//3600}h"
            elif secs > 3600:
                cd = f"{secs//3600}h{(secs%3600)//60:02d}m"
            else:
                cd = f"{secs//60}:{secs%60:02d}"

            cd_font = self._auto_font(cd, fb, fm, self.w - 4)
            cd_h = self._th(cd, cd_font)
            cd_y = cy + (content_h - cd_h) // 2
            self._out(d, (self._cx(cd, cd_font), cd_y), cd, cd_font, self.c_countdown)
        else:
            self._out(d, (self._cx("NO PASSES", fm), cy + content_h // 3), "NO PASSES", fm, self.c_dim)

        self._apply_stars(img, self.hdr_h)
        return img

    def _render_iss_alert(self, data: Dict[str, Any]) -> Image.Image:
        """Full-screen pulsing alert — no header bar, maximum impact."""
        img = Image.new('RGB', (self.w, self.h), (0, 0, 0))
        d = ImageDraw.Draw(img)
        fb = self.fonts['big']
        fm = self.fonts['med']

        pulse = abs((datetime.now().microsecond // 100000) - 5) / 5.0
        r, g, b = self.c_alert
        pc = (int(r * (0.3 + 0.7 * pulse)), int(g * (0.3 + 0.7 * pulse)), int(b * (0.3 + 0.7 * pulse)))

        # Flash border
        if pulse > 0.5:
            d.rectangle([(0, 0), (self.w-1, self.h-1)], outline=self.ACCENT_LINE)

        # Animate icon across top
        if self._iss_icon:
            npass = data.get('next_pass', {})
            progress = 0.5
            if npass.get('start_time') and npass.get('end_time'):
                now = datetime.now(timezone.utc)
                total = (npass['end_time'] - npass['start_time']).total_seconds()
                elapsed = (now - npass['start_time']).total_seconds()
                progress = min(1.0, max(0.0, elapsed / total)) if total > 0 else 0.5
            img.paste(self._iss_icon, (int(progress * (self.w - 8)), 1), self._iss_icon)

        # "ISS" big + "LOOK UP!" medium, centered
        t1, t2 = "ISS", "LOOK UP!"
        t2f = self._auto_font(t2, fm, self.fonts['sm'], self.w - 4)
        h1, h2 = self._th(t1, fb), self._th(t2, t2f)
        gap = 2
        y = (self.h - h1 - gap - h2) // 2
        self._out(d, (self._cx(t1, fb), y), t1, fb, pc)
        self._out(d, (self._cx(t2, t2f), y + h1 + gap), t2, t2f, pc)

        self._apply_stars(img)
        return img

    # ── Launch Countdown ────────────────────────────────────────

    def render_launch(self, data: Dict[str, Any]) -> Image.Image:
        """
        Header bar: Go/TBD/Hold status  |  SpX
        Content: BIG T-countdown
        """
        img = Image.new('RGB', (self.w, self.h), (0, 0, 0))
        d = ImageDraw.Draw(img)

        sid = data.get('status_id', 2)
        sc = self.c_go if sid == 1 else (self.c_hold if sid in (4, 5, 7) else self.c_tbd)

        status = data.get('status', 'TBD')
        abbr = data.get('provider_abbr', '')
        self._draw_header_bar(d, status, abbr, label_color=sc)

        # Countdown
        fb = self.fonts['big']
        fm = self.fonts['med']
        cy = self._content_y()
        content_h = self.h - cy

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
        cd_h = self._th(cd, cd_font)
        cd_y = cy + (content_h - cd_h) // 2
        self._out(d, (self._cx(cd, cd_font), cd_y), cd, cd_font, sc)

        self._apply_stars(img, self.hdr_h)
        return img

    def render_launch_ticker(self, data: Dict[str, Any]) -> Image.Image:
        """Scrolling: header bar persists, mission details scroll below."""
        fm = self.fonts['med']
        fs = self.fonts['sm']

        top = f"{data.get('provider','')} {data.get('rocket','')}"
        bot = f"{data.get('mission','')}  \u00b7  {data.get('pad','')}"

        tw = max(self._tw(top, fm), self._tw(bot, fs), self.w + 1)
        img = Image.new('RGB', (tw, self.h), (0, 0, 0))
        d = ImageDraw.Draw(img)

        h1, h2 = self._th("A", fm), self._th("A", fs)
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
        Scrolling horizontal ticker with header bar.
        [Header: TONIGHT] then [icon] Planet Time ... [constellation art] Name
        """
        fm = self.fonts['med']
        fs = self.fonts['sm']

        icon_size = min(16, self.h - 4)
        entry_gap = 14
        cy = self._content_y()
        content_h = self.h - cy

        # Measure planet entries
        entries = []
        for planet in planets:
            name = planet.get('name', '?')
            rise = planet.get('rise_time', '')
            rise_short = rise.replace(' PM', 'p').replace(' AM', 'a')
            text = f"{name} {rise_short}" if rise_short else name
            text_w = self._tw(text, fm)
            total_w = icon_size + 3 + text_w
            entries.append({'icon_key': name.lower(), 'text': text, 'total_w': total_w})

        # Constellation
        has_const = constellation and constellation.get('stars')
        const_w = max(32, content_h) + entry_gap if has_const else 0

        # Total ticker width (starts after one screen of header)
        ticker_content_w = sum(e['total_w'] + entry_gap for e in entries) + const_w
        total_w = self.w + ticker_content_w  # Header fills first screen, then content scrolls

        img = Image.new('RGB', (total_w, self.h), (0, 0, 0))
        d = ImageDraw.Draw(img)

        # Draw header bar across first screen-width
        d.rectangle([(0, 0), (self.w - 1, self.hdr_h - 2)], fill=self.HEADER_BG)
        d.line([(0, self.hdr_h - 1), (self.w, self.hdr_h - 1)], fill=self.ACCENT_LINE)
        lbl = "TONIGHT"
        ly = (self.hdr_h - 1 - self._th(lbl, fm)) // 2
        self._out(d, (2, max(0, ly)), lbl, fm, self.c_header)

        # Draw planet entries starting after header area
        x = self.w
        for entry in entries:
            icon = self._planet_icons.get(entry['icon_key'])
            icon_y = cy + (content_h - icon_size) // 2
            if icon:
                scaled = icon.resize((icon_size, icon_size), Image.NEAREST)
                img.paste(scaled, (x, icon_y), scaled)

            text_y = cy + (content_h - self._th(entry['text'], fm)) // 2
            self._out(d, (x + icon_size + 3, text_y), entry['text'], fm, self.c_planet)
            x += entry['total_w'] + entry_gap

        # Constellation at end
        if has_const:
            const_draw_w = max(32, content_h)
            cx0, cy0 = x, cy + 1
            cx1, cy1 = x + const_draw_w, self.h - 9
            self._draw_constellation(img, d, constellation, (cx0, cy0, cx1, cy1))

            cname = constellation.get('display_name', '')
            cnx = cx0 + (const_draw_w - self._tw(cname, fs)) // 2
            self._out(d, (max(cx0, cnx), self.h - 9), cname, fs, self.c_cstar)

        self._apply_stars(img, self.hdr_h)
        return img

    def _draw_constellation(self, img, d, const, area):
        x0, y0, x1, y1 = area
        aw, ah = x1 - x0, y1 - y0
        pad = 2
        stars = const.get('stars', [])
        lines = const.get('lines', [])
        if not stars:
            return

        pts = [(x0 + pad + int(s['x']/100*(aw-pad*2)),
                y0 + pad + int(s['y']/100*(ah-pad*2))) for s in stars]

        for a, b in lines:
            if a < len(pts) and b < len(pts):
                d.line([pts[a], pts[b]], fill=self.c_cline, width=1)

        for i, (px, py) in enumerate(pts):
            mag = stars[i].get('mag', 2.0) if i < len(stars) else 2.0
            if mag < 0.5:
                d.rectangle([px-1, py-1, px+1, py+1], fill=self.c_cstar)
                for gx, gy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
                    try: d.point((px+gx, py+gy), fill=self.c_cline)
                    except: pass
            elif mag < 1.5:
                d.rectangle([px, py, px+1, py+1], fill=self.c_cstar)
            else:
                d.point((px, py), fill=self.c_cstar)

    # ── People in Space ─────────────────────────────────────────

    def render_people_in_space(self, people: List[Dict[str, str]]) -> Image.Image:
        """Header bar with count, scrolling crew names below."""
        fm = self.fonts['med']
        fs = self.fonts['sm']

        count = len(people)
        names = "  \u00b7  ".join(p.get('name', '?') for p in people)
        header_text = f"{count} IN SPACE"

        names_w = self._tw(names, fs)
        total_w = self.w + names_w + self.w  # Header screen + names + trailing space

        img = Image.new('RGB', (total_w, self.h), (0, 0, 0))
        d = ImageDraw.Draw(img)

        # Header bar on first screen
        d.rectangle([(0, 0), (self.w - 1, self.hdr_h - 2)], fill=self.HEADER_BG)
        d.line([(0, self.hdr_h - 1), (self.w, self.hdr_h - 1)], fill=self.ACCENT_LINE)

        if self._iss_icon:
            iy = (self.hdr_h - 8) // 2
            img.paste(self._iss_icon, (2, max(0, iy)), self._iss_icon)
            ly = (self.hdr_h - 1 - self._th(header_text, fm)) // 2
            self._out(d, (12, max(0, ly)), header_text, fm, self.c_countdown)
        else:
            ly = (self.hdr_h - 1 - self._th(header_text, fm)) // 2
            self._out(d, (2, max(0, ly)), header_text, fm, self.c_countdown)

        # Names scroll in content area
        cy = self._content_y()
        content_h = self.h - cy
        ny = cy + (content_h - self._th("A", fs)) // 2
        d.text((self.w, ny), names, fill=self.c_planet, font=fs)

        self._apply_stars(img, self.hdr_h)
        return img

    # ── APOD ────────────────────────────────────────────────────

    def render_apod(self, data: Dict[str, Any]) -> Image.Image:
        """Header: NASA APOD, scrolling title below."""
        fm = self.fonts['med']
        fs = self.fonts['sm']

        title = data.get('title', 'Astronomy Picture of the Day')
        date = data.get('date', '')

        title_w = self._tw(title, fm)
        total_w = self.w + title_w + self.w

        img = Image.new('RGB', (total_w, self.h), (0, 0, 0))
        d = ImageDraw.Draw(img)

        # Header
        label = "NASA APOD"
        d.rectangle([(0, 0), (self.w - 1, self.hdr_h - 2)], fill=self.HEADER_BG)
        d.line([(0, self.hdr_h - 1), (self.w, self.hdr_h - 1)], fill=self.ACCENT_LINE)
        ly = (self.hdr_h - 1 - self._th(label, fm)) // 2
        self._out(d, (2, max(0, ly)), label, fm, self.c_header)
        if date:
            dw = self._tw(date, fs)
            dy = (self.hdr_h - 1 - self._th(date, fs)) // 2
            d.text((self.w - dw - 2, max(0, dy)), date, fill=self.c_dim, font=fs)

        # Title scrolls in content area
        cy = self._content_y()
        content_h = self.h - cy
        ty = cy + (content_h - self._th("A", fm)) // 2
        self._out(d, (self.w, ty), title, fm, self.c_countdown)

        self._apply_stars(img, self.hdr_h)
        return img

    # ── Fallback ────────────────────────────────────────────────

    def render_no_data(self) -> Image.Image:
        img = Image.new('RGB', (self.w, self.h), (0, 0, 0))
        d = ImageDraw.Draw(img)
        fb = self.fonts['big']
        fm = self.fonts['med']
        fs = self.fonts['sm']

        t1 = "SPACE"
        t2 = self._fit("SET LOCATION", fs, self.w - 4)
        t1f = self._auto_font(t1, fb, fm, self.w - 4)

        h1, h2 = self._th(t1, t1f), self._th(t2, fs)
        gap = 3
        y = (self.h - h1 - gap - h2) // 2

        self._out(d, (self._cx(t1, t1f), y), t1, t1f, self.c_header)
        d.text((self._cx(t2, fs), y + h1 + gap), t2, fill=self.c_dim, font=fs)

        self._apply_stars(img)
        return img
