"""
Stock News Ticker Plugin for LEDMatrix

Displays scrolling stock-specific news headlines and financial updates from RSS feeds.
Shows market news, company announcements, and financial updates for tracked stocks.

Features:
- Real Yahoo Finance RSS headlines per stock symbol
- Company logo rendering (logo+ticker, ticker only, logo only modes)
- Configurable per-day / per-hour request budget
- ScrollHelper-based horizontal scrolling at high FPS
- Vegas scroll mode integration
- Custom RSS feed support
- Fully configurable colors, fonts, scroll speed, and display style
"""

import logging
import random
import time
import requests
import xml.etree.ElementTree as ET
import html
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.plugin_system.base_plugin import BasePlugin
from src.common.scroll_helper import ScrollHelper
from src.common.logo_helper import LogoHelper

logger = logging.getLogger(__name__)

_YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
_YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"


class StockNewsTickerPlugin(BasePlugin):
    """
    Stock news ticker plugin for displaying real financial headlines.

    Fetches live headlines from Yahoo Finance RSS per symbol and renders them
    as a scrolling horizontal ticker with optional company logos.

    Configuration options:
        global: Scroll, display, rate-limit, and logo settings
        feeds: Stock symbols, custom RSS feeds, and text colors
    """

    def __init__(self, plugin_id: str, config: Dict[str, Any],
                 display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        self.feeds_config = config.get('feeds', {})
        self.global_config = config.get('global', {})

        # Display dimensions
        self.display_width = self.display_manager.matrix.width
        self.display_height = self.display_manager.matrix.height

        # Scroll / display settings
        self.display_duration = self.global_config.get('display_duration', 30)
        self.scroll_speed = self.global_config.get('scroll_speed', 1)
        self.scroll_delay = self.global_config.get('scroll_delay', 0.01)
        self.dynamic_duration = self.global_config.get('dynamic_duration', True)
        self.min_duration = self.global_config.get('min_duration', 30)
        self.max_duration = self.global_config.get('max_duration', 300)
        self.max_headlines_per_symbol = self.global_config.get('max_headlines_per_symbol', 1)
        self.headlines_per_rotation = self.global_config.get('headlines_per_rotation', 2)
        self.font_size = self.global_config.get('font_size', 10)
        self.rotation_enabled = self.global_config.get('rotation_enabled', True)
        self.rotation_threshold = self.global_config.get('rotation_threshold', 1)
        self.shuffle_headlines = self.global_config.get('shuffle_headlines', True)
        self.show_publisher = self.global_config.get('show_publisher', True)

        # Fetch rate limiting
        self.update_interval = self.global_config.get('update_interval_seconds', 900)
        self.max_daily_requests = self.global_config.get('max_daily_requests', 200)
        self.max_requests_per_hour = self.global_config.get('max_requests_per_hour', 50)
        self._requests_today: int = 0
        self._last_reset_date: str = ''
        self._request_timestamps: list = []

        # Display style
        self.display_style = self.global_config.get('display_style', 'logo_and_ticker')
        self.logo_size = self.global_config.get('logo_size', min(self.display_height, 32))
        self.logo_fetch_enabled = self.global_config.get('logo_fetch_enabled', True)
        self.logo_url_template = self.global_config.get(
            'logo_url_template',
            'https://financialmodelingprep.com/image-stock/{symbol}.png'
        )

        # Colors
        self.text_color = tuple(self.feeds_config.get('text_color', [0, 255, 0]))
        self.symbol_color = tuple(self.feeds_config.get('symbol_color', [255, 255, 0]))

        # Background service config (kept for timeout setting)
        self.background_config = self.global_config.get('background_service', {
            'enabled': True,
            'request_timeout': 30,
        })

        # State
        self.all_news_items: list = []
        self.current_rotation_index: int = 0
        self._rotation_count: int = 0
        self._items_rotated: int = 0   # tracks full-cycle completions for shuffle
        self._cycle_complete: bool = False
        self.last_update: float = 0
        self.initialized = True

        # HTTP session with retry/backoff
        self._session = self._create_session()

        # Logo infrastructure
        self._logo_dir = Path(__file__).parent / 'assets' / 'logos'
        self._logo_dir.mkdir(parents=True, exist_ok=True)
        self.logo_helper = LogoHelper(self.display_width, self.display_height, logger=self.logger)

        # Fonts and scroll helper
        self._fonts: dict = self._load_fonts()
        self.scroll_helper = ScrollHelper(self.display_width, self.display_height, logger=self.logger)
        self._configure_scroll_settings()

        # Register fonts with font manager for UI introspection
        self._register_fonts()

        stock_symbols = self.feeds_config.get('stock_symbols', [])
        custom_feeds = list(self.feeds_config.get('custom_feeds', {}).keys())
        self.logger.info(
            "Stock news ticker initialized — symbols=%s, custom_feeds=%s, style=%s",
            stock_symbols, custom_feeds, self.display_style,
        )

    # -------------------------------------------------------------------------
    # Font / scroll configuration
    # -------------------------------------------------------------------------

    def _load_fonts(self) -> dict:
        """Load PIL fonts for rendering."""
        try:
            font_path = 'assets/fonts/PressStart2P-Regular.ttf'
            return {
                'headline': ImageFont.truetype(font_path, self.font_size),
                'symbol': ImageFont.truetype(font_path, self.font_size),
            }
        except IOError:
            self.logger.warning("Press Start 2P font not found, using PIL default")
            return {}

    def _configure_scroll_settings(self) -> None:
        """Configure ScrollHelper with plugin scroll settings (supports legacy keys)."""
        if 'scroll_pixels_per_second' in self.global_config:
            pixels_per_second = float(self.global_config['scroll_pixels_per_second'])
        elif self.scroll_delay and self.scroll_delay > 0:
            pixels_per_second = self.scroll_speed / self.scroll_delay
        else:
            pixels_per_second = 25.0
        self.scroll_helper.set_scroll_speed(pixels_per_second)

        if 'scroll_target_fps' in self.global_config:
            target_fps = float(self.global_config['scroll_target_fps'])
        elif self.scroll_delay and self.scroll_delay > 0:
            target_fps = 1.0 / self.scroll_delay
        else:
            target_fps = 100.0
        if hasattr(self.scroll_helper, 'set_target_fps'):
            self.scroll_helper.set_target_fps(target_fps)

        self.scroll_helper.set_dynamic_duration_settings(
            enabled=self.dynamic_duration,
            min_duration=self.min_duration,
            max_duration=self.max_duration,
        )

    def _register_fonts(self) -> None:
        """Register fonts with the font manager for UI introspection."""
        try:
            if not hasattr(self.plugin_manager, 'font_manager'):
                return
            fm = self.plugin_manager.font_manager
            fm.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.headline",
                family="press_start", size_px=self.font_size, color=self.text_color,
            )
            fm.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.symbol",
                family="press_start", size_px=self.font_size, color=self.symbol_color,
            )
            fm.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.info",
                family="four_by_six", size_px=6, color=(150, 150, 150),
            )
        except Exception as e:
            self.logger.warning(f"Error registering fonts: {e}")

    def _create_session(self) -> requests.Session:
        """Build a requests Session with automatic retry and exponential backoff."""
        session = requests.Session()
        retry = Retry(
            total=4,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({'User-Agent': 'LEDMatrix/2.0 (stock-news plugin)'})
        return session

    # -------------------------------------------------------------------------
    # Update / data fetching
    # -------------------------------------------------------------------------

    def update(self) -> None:
        """Fetch fresh headlines for all tracked symbols and custom feeds."""
        if not self.initialized:
            return
        if time.time() - self.last_update < self.update_interval:
            return

        try:
            all_items: list = []
            stock_symbols = self.feeds_config.get('stock_symbols', [])

            for symbol in stock_symbols:
                items = self._fetch_stock_news(symbol)
                all_items.extend(items)

            custom_feeds = self.feeds_config.get('custom_feeds', {})
            for feed_name, feed_url in custom_feeds.items():
                items = self._fetch_rss_feed(
                    feed_name, feed_url, max_items=self.headlines_per_rotation
                )
                all_items.extend(items)

            max_items = (
                len(stock_symbols) * self.max_headlines_per_symbol
                + len(custom_feeds) * self.headlines_per_rotation
            )
            self.all_news_items = all_items[:max_items] if len(all_items) > max_items else all_items
            if self.shuffle_headlines and self.all_news_items:
                random.shuffle(self.all_news_items)
            self.current_rotation_index = 0
            self._rotation_count = 0
            self._items_rotated = 0
            self.last_update = time.time()
            self.logger.info("[Stock News] Updated: %d items", len(self.all_news_items))

            if hasattr(self, 'scroll_helper'):
                self.scroll_helper.clear_cache()
                self.scroll_helper.reset_scroll()

        except Exception as e:
            self.logger.error(f"[Stock News] update() error: {e}")

    def _fetch_stock_news(self, symbol: str) -> List[Dict]:
        """Fetch live headlines for a symbol — tries YF search API first, falls back to RSS."""
        items = self._fetch_yf_api(symbol)
        if items:
            return items
        # RSS fallback (e.g. if API changes or rate-limits)
        self.logger.debug("[Stock News] RSS fallback for %s", symbol)
        url = _YAHOO_RSS.format(symbol=symbol)
        items = self._fetch_rss_feed(symbol, url, max_items=self.max_headlines_per_symbol)
        for item in items:
            item['symbol'] = symbol
        return items

    def _fetch_yf_api(self, symbol: str) -> List[Dict]:
        """Fetch headlines via Yahoo Finance search API (returns publisher + timestamp)."""
        bucket = int(time.time() // self.update_interval)
        cache_key = f"stock_yf_{symbol}_{bucket}"
        cached = self.cache_manager.get(cache_key)
        if cached:
            self.logger.debug("[Stock News] Cache hit (YF API): %s", symbol)
            return cached

        if not self._check_request_budget():
            self.logger.warning(
                "[Stock News] Request budget exceeded (%d/day, %d/hr limit) — skipping %s",
                self._requests_today, self.max_requests_per_hour, symbol,
            )
            return []

        params = {
            'q': symbol,
            'lang': 'en-US',
            'region': 'US',
            'quotesCount': 0,
            'newsCount': self.max_headlines_per_symbol,
            'enableFuzzyQuery': False,
        }
        try:
            timeout = self.background_config.get('request_timeout', 30)
            resp = self._session.get(_YAHOO_SEARCH, params=params, timeout=timeout)
            resp.raise_for_status()
            self._record_request()

            news_raw = resp.json().get('news', [])
            items: List[Dict] = []
            for raw in news_raw[:self.max_headlines_per_symbol]:
                title = raw.get('title', '').strip()
                if not title:
                    continue
                pub_ts = raw.get('providerPublishTime', 0)
                pub_dt = datetime.fromtimestamp(pub_ts) if pub_ts else None
                items.append({
                    'symbol': symbol,
                    'feed_name': symbol,
                    'title': self._clean_headline(title),
                    'link': raw.get('link', ''),
                    'publisher': raw.get('publisher', ''),
                    'published': pub_dt.isoformat() if pub_dt else '',
                    'published_dt': pub_dt,
                    'summary': raw.get('summary', ''),
                })

            self.cache_manager.set(cache_key, items, ttl=self.update_interval * 2)
            self.logger.info("[Stock News] YF API: %d items for %s", len(items), symbol)
            return items

        except requests.RequestException as e:
            self.logger.warning("[Stock News] YF API network error for %s: %s", symbol, e)
        except (ValueError, KeyError) as e:
            self.logger.warning("[Stock News] YF API parse error for %s: %s", symbol, e)
        except Exception as e:
            self.logger.warning("[Stock News] YF API unexpected error for %s: %s", symbol, e)
        return []

    def _fetch_rss_feed(self, feed_name: str, url: str, max_items: int = 3) -> List[Dict]:
        """Fetch and parse an RSS feed with caching and request budget enforcement."""
        bucket = int(time.time() // self.update_interval)
        cache_key = f"stock_rss_{feed_name}_{bucket}"
        cached = self.cache_manager.get(cache_key)
        if cached:
            self.logger.debug("[Stock News] Cache hit (RSS): %s", feed_name)
            return cached

        if not self._check_request_budget():
            self.logger.warning(
                "[Stock News] Request budget exceeded — skipping %s", feed_name,
            )
            return []

        try:
            self.logger.info("[Stock News] Fetching RSS: %s", feed_name)
            timeout = self.background_config.get('request_timeout', 30)
            resp = self._session.get(url, timeout=timeout)
            resp.raise_for_status()
            self._record_request()

            root = ET.fromstring(resp.content)
            items: List[Dict] = []
            for rss_item in root.findall('.//item')[:max_items]:
                title_el = rss_item.find('title')
                if title_el is None or not title_el.text:
                    continue
                link_el = rss_item.find('link')
                pub_el = rss_item.find('pubDate')
                items.append({
                    'feed_name': feed_name,
                    'title': self._clean_headline(html.unescape(title_el.text.strip())),
                    'link': (link_el.text or '') if link_el is not None else '',
                    'publisher': '',
                    'published': (pub_el.text or '') if pub_el is not None else '',
                })

            self.cache_manager.set(cache_key, items, ttl=self.update_interval * 2)
            self.logger.info("[Stock News] RSS: %d items for %s", len(items), feed_name)
            return items

        except requests.RequestException as e:
            self.logger.error("[Stock News] RSS fetch error for %s: %s", feed_name, e)
        except ET.ParseError as e:
            self.logger.error("[Stock News] RSS parse error for %s: %s", feed_name, e)
        except Exception as e:
            self.logger.error("[Stock News] Unexpected error for %s: %s", feed_name, e)
        return []

    def _check_request_budget(self) -> bool:
        """Return True if within daily and hourly request limits."""
        today = datetime.now().strftime('%Y-%m-%d')
        if self._last_reset_date != today:
            self._requests_today = 0
            self._last_reset_date = today

        if self._requests_today >= self.max_daily_requests:
            return False

        cutoff = time.time() - 3600
        self._request_timestamps = [t for t in self._request_timestamps if t > cutoff]
        if len(self._request_timestamps) >= self.max_requests_per_hour:
            return False

        return True

    def _record_request(self) -> None:
        self._requests_today += 1
        self._request_timestamps.append(time.time())

    def _clean_headline(self, headline: str) -> str:
        """Normalise whitespace and trim headline to display-safe length."""
        if not headline:
            return ""
        headline = re.sub(r'\s+', ' ', headline.strip())
        headline = re.sub(r'^\s*-\s*', '', headline)
        if len(headline) > 80:
            headline = headline[:77] + "..."
        return headline

    # -------------------------------------------------------------------------
    # Logo fetching
    # -------------------------------------------------------------------------

    def _get_symbol_logo(self, symbol: str) -> Optional[Image.Image]:
        """Return a company logo PIL Image for the symbol, downloading if needed."""
        if not self.logo_fetch_enabled:
            return None

        logo_path = self._logo_dir / f"{symbol}.png"
        if logo_path.exists():
            return self.logo_helper.load_logo(
                symbol, logo_path,
                max_width=self.logo_size, max_height=self.logo_size,
            )

        url = self.logo_url_template.replace('{symbol}', symbol)
        try:
            resp = self._session.get(url, timeout=10)
            if resp.status_code == 200 and resp.content:
                logo_path.write_bytes(resp.content)
                self.logger.info("[Stock News] Downloaded logo for %s", symbol)
                return self.logo_helper.load_logo(
                    symbol, logo_path,
                    max_width=self.logo_size, max_height=self.logo_size,
                )
        except Exception as e:
            self.logger.debug("[Stock News] Logo fetch failed for %s: %s", symbol, e)

        return None

    # -------------------------------------------------------------------------
    # Display
    # -------------------------------------------------------------------------

    def display(self, display_mode: str = None, force_clear: bool = False) -> None:
        """Display the scrolling stock news ticker."""
        if not self.initialized:
            self._display_error("Stock news ticker not initialized")
            return

        if not self.all_news_items:
            self._display_no_news()
            return

        if not self.scroll_helper.cached_image or force_clear:
            self._create_scrolling_image()
            if not self.scroll_helper.cached_image:
                self._display_no_news()
                return
            self._cycle_complete = False

        if force_clear:
            self.scroll_helper.reset_scroll()
            self._cycle_complete = False

        self.display_manager.set_scrolling_state(True)
        self.display_manager.process_deferred_updates()

        self.scroll_helper.update_scroll_position()

        if self.scroll_helper.is_scroll_complete():
            self._cycle_complete = True
            if self.rotation_enabled:
                self._rotation_count += 1
                if self._rotation_count >= self.rotation_threshold:
                    self._rotation_count = 0
                    if len(self.all_news_items) > 1:
                        self.all_news_items = self.all_news_items[1:] + self.all_news_items[:1]
                        self._items_rotated += 1
                        # After every item has had a turn at the front, reshuffle
                        if self.shuffle_headlines and self._items_rotated >= len(self.all_news_items):
                            random.shuffle(self.all_news_items)
                            self._items_rotated = 0
                            self.logger.debug("[Stock News] Reshuffled headlines")
                    self.scroll_helper.clear_cache()
                    self.scroll_helper.reset_scroll()
                    return  # next display() call rebuilds with rotated order

        visible_portion = self.scroll_helper.get_visible_portion()
        if visible_portion:
            self.display_manager.image.paste(visible_portion, (0, 0))
            self.display_manager.update_display()

        self.scroll_helper.log_frame_rate()

    def _create_scrolling_image(self) -> None:
        """Build the wide horizontal ticker image from all current news items."""
        try:
            item_images = [
                img for img in (self._render_news_item(item) for item in self.all_news_items)
                if img is not None
            ]
            if not item_images:
                self.scroll_helper.clear_cache()
                return
            self.scroll_helper.create_scrolling_image(item_images, item_gap=48)
            self._cycle_complete = False
            self.logger.info(
                "[Stock News] Ticker image built: %d items, %dpx wide",
                len(item_images), self.scroll_helper.total_scroll_width,
            )
        except Exception as e:
            self.logger.error(f"[Stock News] Failed to build ticker image: {e}")
            self.scroll_helper.clear_cache()

    def _render_news_item(self, news_item: dict) -> Optional[Image.Image]:
        """
        Render one news item as a full-height PIL image strip.

        display_style controls what is shown:
          logo_and_ticker — logo (if available) + SYMBOL: headline
          ticker_only     — SYMBOL: headline (no logo lookup)
          logo_only       — logo (if available) + SYMBOL (no headline text)
        """
        try:
            symbol = news_item.get('symbol', news_item.get('feed_name', '?'))
            title = news_item.get('title', '')
            font_sym = self._fonts.get('symbol')
            font_hl = self._fonts.get('headline')

            logo = (
                self._get_symbol_logo(symbol)
                if self.display_style in ('logo_and_ticker', 'logo_only')
                else None
            )

            publisher = news_item.get('publisher', '')
            if self.display_style == 'logo_only':
                symbol_text = f" {symbol}"
                headline_text = ''
            else:
                symbol_text = f"{symbol}: "
                headline_text = title
                if self.show_publisher and publisher:
                    headline_text = f"{headline_text}  •  {publisher}"

            draw_tmp = ImageDraw.Draw(Image.new('RGB', (1, 1)))
            sym_bbox = draw_tmp.textbbox((0, 0), symbol_text, font=font_sym) if symbol_text else (0, 0, 0, 0)
            hl_bbox = draw_tmp.textbbox((0, 0), headline_text, font=font_hl) if headline_text else (0, 0, 0, 0)

            sym_w = sym_bbox[2] - sym_bbox[0]
            hl_w = hl_bbox[2] - hl_bbox[0]
            text_h = max(sym_bbox[3] - sym_bbox[1], hl_bbox[3] - hl_bbox[1], 1)
            gap = 4
            logo_w = (logo.width + gap) if logo else 0
            total_w = max(logo_w + sym_w + (gap if headline_text else 0) + hl_w, 1)

            img = Image.new('RGB', (total_w, self.display_height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            text_y = max(0, (self.display_height - text_h) // 2)

            x = 0
            if logo:
                logo_y = max(0, (self.display_height - logo.height) // 2)
                mask = logo if logo.mode == 'RGBA' else None
                img.paste(logo, (x, logo_y), mask)
                x += logo.width + gap

            if symbol_text:
                draw.text((x, text_y), symbol_text, fill=self.symbol_color, font=font_sym)
                x += sym_w + (gap if headline_text else 0)
            if headline_text:
                draw.text((x, text_y), headline_text, fill=self.text_color, font=font_hl)

            return img

        except Exception as e:
            self.logger.warning(f"[Stock News] Render error: {e}")
            return None

    # -------------------------------------------------------------------------
    # Vegas scroll mode
    # -------------------------------------------------------------------------

    def get_vegas_content(self) -> Optional[list]:
        """Return list of item images for Vegas continuous scroll."""
        if not self.all_news_items:
            return None
        images = [self._render_news_item(item) for item in self.all_news_items]
        result = [img for img in images if img is not None]
        return result if result else None

    def get_vegas_content_type(self) -> str:
        return 'multi'

    # -------------------------------------------------------------------------
    # Fallback displays
    # -------------------------------------------------------------------------

    def _display_no_news(self) -> None:
        img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text((5, 12), "No Stock News", fill=(150, 150, 150))
        self.display_manager.image = img.copy()
        self.display_manager.update_display()

    def _display_error(self, message: str) -> None:
        img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text((5, 12), message, fill=(255, 0, 0))
        self.display_manager.image = img.copy()
        self.display_manager.update_display()

    # -------------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------------

    def get_display_duration(self) -> float:
        return self.display_duration

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update({
            'total_news_items': len(self.all_news_items),
            'stock_symbols': self.feeds_config.get('stock_symbols', []),
            'custom_feeds': list(self.feeds_config.get('custom_feeds', {}).keys()),
            'last_update': self.last_update,
            'display_style': self.display_style,
            'requests_today': self._requests_today,
            'requests_this_hour': len(self._request_timestamps),
            'max_daily_requests': self.max_daily_requests,
            'max_requests_per_hour': self.max_requests_per_hour,
            'update_interval_seconds': self.update_interval,
            'scroll_speed': self.scroll_speed,
            'font_size': self.font_size,
            'text_color': self.text_color,
            'symbol_color': self.symbol_color,
        })
        return info

    def cleanup(self) -> None:
        self.all_news_items = []
        self.logger.info("[Stock News] Plugin cleaned up")
