"""
Stock News Ticker Plugin for LEDMatrix

Displays scrolling stock-specific news headlines and financial updates from RSS feeds.
Shows market news, company announcements, and financial updates for tracked stocks.

Features:
- Stock-specific RSS feeds and news aggregation
- Symbol tracking and filtering
- Scrolling headline display
- Custom RSS feed support
- Configurable scroll speed and colors
- Background data fetching

API Version: 1.0.0
"""

import logging
import time
import requests
import xml.etree.ElementTree as ET
import html
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
from PIL import Image, ImageDraw, ImageFont

from src.plugin_system.base_plugin import BasePlugin
from src.common.scroll_helper import ScrollHelper

logger = logging.getLogger(__name__)


class StockNewsTickerPlugin(BasePlugin):
    """
    Stock news ticker plugin for displaying financial headlines.

    Tracks specific stock symbols and displays relevant news headlines
    from financial RSS feeds with configurable display options.

    Configuration options:
        feeds: Stock symbols to track and custom RSS feeds
        display_options: Scroll speed, duration, colors
        background_service: Data fetching configuration
    """

    def __init__(self, plugin_id: str, config: Dict[str, Any],
                 display_manager, cache_manager, plugin_manager):
        """Initialize the stock news ticker plugin."""
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        # Configuration
        self.feeds_config = config.get('feeds', {})
        self.global_config = config.get('global', {})

        # Display settings
        self.display_duration = self.global_config.get('display_duration', 30)
        self.scroll_speed = self.global_config.get('scroll_speed', 1)
        self.scroll_delay = self.global_config.get('scroll_delay', 0.01)
        self.dynamic_duration = self.global_config.get('dynamic_duration', True)
        self.min_duration = self.global_config.get('min_duration', 30)
        self.max_duration = self.global_config.get('max_duration', 300)
        self.max_headlines_per_symbol = self.global_config.get('max_headlines_per_symbol', 1)
        self.headlines_per_rotation = self.global_config.get('headlines_per_rotation', 2)
        self.font_size = self.global_config.get('font_size', 10)

        # Display dimensions
        self.display_width = self.display_manager.matrix.width
        self.display_height = self.display_manager.matrix.height

        # Colors
        self.text_color = tuple(self.feeds_config.get('text_color', [0, 255, 0]))
        self.symbol_color = tuple(self.feeds_config.get('symbol_color', [255, 255, 0]))
        self.separator_color = tuple(self.feeds_config.get('separator_color', [255, 0, 0]))

        # Background service configuration
        self.background_config = self.global_config.get('background_service', {
            'enabled': True,
            'request_timeout': 30,
            'max_retries': 5,
            'priority': 2
        })

        # State
        self.current_news_items = []
        self.current_news_group = 0
        self.scroll_position = 0
        self.last_update = 0
        self.all_news_items = []
        self.current_rotation_index = 0
        self._cycle_complete = False
        self.initialized = True

        # Fonts and scroll
        self._fonts: dict = self._load_fonts()
        self.scroll_helper = ScrollHelper(self.display_width, self.display_height, logger=self.logger)
        self._configure_scroll_settings()

        # Register fonts with font manager for UI introspection
        self._register_fonts()

        # Log configuration
        stock_symbols = self.feeds_config.get('stock_symbols', [])
        custom_feeds = list(self.feeds_config.get('custom_feeds', {}).keys())

        self.logger.info("Stock news ticker plugin initialized")
        self.logger.info(f"Tracking symbols: {stock_symbols}")
        self.logger.info(f"Custom feeds: {custom_feeds}")

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
        """Configure ScrollHelper with plugin scroll settings."""
        pixels_per_second = self.global_config.get('scroll_pixels_per_second', 25.0)
        self.scroll_helper.set_scroll_speed(pixels_per_second)
        target_fps = self.global_config.get('scroll_target_fps', 100.0)
        if hasattr(self.scroll_helper, 'set_target_fps'):
            self.scroll_helper.set_target_fps(target_fps)
        self.scroll_helper.set_dynamic_duration_settings(
            enabled=self.dynamic_duration,
            min_duration=self.min_duration,
            max_duration=self.max_duration,
        )

    def _register_fonts(self):
        """Register fonts with the font manager."""
        try:
            if not hasattr(self.plugin_manager, 'font_manager'):
                return

            font_manager = self.plugin_manager.font_manager

            # Headline font
            font_manager.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.headline",
                family="press_start",
                size_px=self.font_size,
                color=self.text_color
            )

            # Symbol font
            font_manager.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.symbol",
                family="press_start",
                size_px=self.font_size,
                color=self.symbol_color
            )

            # Separator font
            font_manager.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.separator",
                family="press_start",
                size_px=self.font_size,
                color=self.separator_color
            )

            # Info font (source, time)
            font_manager.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.info",
                family="four_by_six",
                size_px=6,
                color=(150, 150, 150)
            )

            self.logger.info("Stock news ticker fonts registered")
        except Exception as e:
            self.logger.warning(f"Error registering fonts: {e}")

    def update(self) -> None:
        """Update stock news headlines for all tracked symbols."""
        if not self.initialized:
            return

        try:
            self.current_news_items = []
            self.all_news_items = []

            # Get stock symbols to track
            stock_symbols = self.feeds_config.get('stock_symbols', [])

            # Fetch news for each symbol
            for symbol in stock_symbols:
                symbol_news = self._fetch_stock_news(symbol)
                if symbol_news:
                    self.all_news_items.extend(symbol_news)

            # Fetch from custom feeds
            custom_feeds = self.feeds_config.get('custom_feeds', {})
            for feed_name, feed_url in custom_feeds.items():
                custom_news = self._fetch_feed_headlines(feed_name, feed_url)
                if custom_news:
                    self.all_news_items.extend(custom_news)

            # Limit total news items and reset rotation tracking
            max_items = len(stock_symbols) * self.max_headlines_per_symbol + len(custom_feeds) * self.headlines_per_rotation
            if len(self.all_news_items) > max_items:
                self.all_news_items = self.all_news_items[:max_items]

            # Reset rotation tracking for new content
            if self.all_news_items:
                self.current_rotation_index = 0

            self.last_update = time.time()
            self.logger.debug(f"Updated stock news: {len(self.all_news_items)} total items")
            if hasattr(self, 'scroll_helper'):
                self.scroll_helper.clear_cache()

        except Exception as e:
            self.logger.error(f"Error updating stock news: {e}")

    def _fetch_stock_news(self, symbol: str) -> List[Dict]:
        """Fetch news for a specific stock symbol."""
        cache_key = f"stock_news_{symbol}_{datetime.now().strftime('%Y%m%d%H')}"
        try:
            update_interval = int(self.global_config.get('update_interval_seconds', 300))
        except (ValueError, TypeError):
            update_interval = 300

        # Check cache first
        cached_data = self.cache_manager.get(cache_key)
        if cached_data and (time.time() - self.last_update) < update_interval:
            self.logger.debug(f"Using cached news for {symbol}")
            return cached_data

        try:
            # For now, return placeholder data since actual stock news APIs would require API keys
            # In a real implementation, this would call financial news APIs
            placeholder_news = [
                {
                    'symbol': symbol,
                    'title': f"{symbol} Reports Strong Quarterly Earnings",
                    'summary': f"{symbol} announces better than expected results",
                    'source': 'Financial News',
                    'published': datetime.now().isoformat(),
                    'url': f'https://example.com/news/{symbol}'
                }
            ]

            # Cache the results
            self.cache_manager.set(cache_key, placeholder_news, ttl=update_interval * 2)

            return placeholder_news

        except Exception as e:
            self.logger.error(f"Error fetching news for {symbol}: {e}")
            return []

    def _fetch_feed_headlines(self, feed_name: str, feed_url: str) -> List[Dict]:
        """Fetch headlines from a custom RSS feed."""
        cache_key = f"stock_feed_{feed_name}_{datetime.now().strftime('%Y%m%d%H')}"
        try:
            update_interval = int(self.global_config.get('update_interval_seconds', 300))
        except (ValueError, TypeError):
            update_interval = 300

        # Check cache first
        cached_data = self.cache_manager.get(cache_key)
        if cached_data and (time.time() - self.last_update) < update_interval:
            self.logger.debug(f"Using cached headlines for {feed_name}")
            return cached_data

        try:
            self.logger.info(f"Fetching stock headlines from {feed_name}...")
            response = requests.get(feed_url, timeout=self.background_config.get('request_timeout', 30))
            response.raise_for_status()

            # Parse RSS XML
            root = ET.fromstring(response.content)
            headlines = []

            # Extract headlines from RSS items
            for item in root.findall('.//item')[:self.headlines_per_rotation]:
                title = item.find('title')
                description = item.find('description')
                pub_date = item.find('pubDate')
                link = item.find('link')

                if title is not None and title.text:
                    headline = {
                        'feed_name': feed_name,
                        'title': html.unescape(title.text).strip(),
                        'description': html.unescape(description.text).strip() if description is not None else '',
                        'published': pub_date.text if pub_date is not None else '',
                        'link': link.text if link is not None else '',
                        'timestamp': datetime.now().isoformat()
                    }

                    # Clean up the title
                    headline['title'] = self._clean_headline(headline['title'])
                    headlines.append(headline)

            # Cache the results
            self.cache_manager.set(cache_key, headlines, ttl=update_interval * 2)

            return headlines

        except requests.RequestException as e:
            self.logger.error(f"Error fetching RSS feed {feed_name}: {e}")
            return []
        except ET.ParseError as e:
            self.logger.error(f"Error parsing RSS feed {feed_name}: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Error processing RSS feed {feed_name}: {e}")
            return []

    def _clean_headline(self, headline: str) -> str:
        """Clean and format headline text."""
        if not headline:
            return ""

        # Remove extra whitespace
        headline = re.sub(r'\s+', ' ', headline.strip())

        # Remove common artifacts
        headline = re.sub(r'^\s*-\s*', '', headline)  # Remove leading dashes
        headline = re.sub(r'\s+', ' ', headline)  # Normalize whitespace

        # Limit length for display
        if len(headline) > 80:
            headline = headline[:77] + "..."

        return headline

    def display(self, display_mode: str = None, force_clear: bool = False) -> None:
        """Display scrolling stock news headlines."""
        if not self.initialized:
            self._display_error("Stock news ticker plugin not initialized")
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
        if self.dynamic_duration and self.scroll_helper.is_scroll_complete():
            self._cycle_complete = True

        visible_portion = self.scroll_helper.get_visible_portion()
        if visible_portion:
            self.display_manager.image.paste(visible_portion, (0, 0))
            self.display_manager.update_display()

        self.scroll_helper.log_frame_rate()

    def _create_scrolling_image(self) -> None:
        """Build wide horizontal ticker image from all news items."""
        try:
            item_images = []
            for news_item in self.all_news_items:
                img = self._render_news_item(news_item)
                if img:
                    item_images.append(img)
            if not item_images:
                self.scroll_helper.clear_cache()
                return
            self.scroll_helper.create_scrolling_image(item_images, item_gap=48)
            self._cycle_complete = False
            self.logger.info(
                "Created stock news image: %d items, scroll_width=%dpx",
                len(item_images), self.scroll_helper.total_scroll_width,
            )
        except Exception as e:
            self.logger.error(f"Error creating stock news image: {e}")
            self.scroll_helper.clear_cache()

    def _render_news_item(self, news_item: dict) -> Optional[Image.Image]:
        """Render one news item as a PIL image strip (symbol in yellow, headline in green)."""
        try:
            symbol = news_item.get('symbol', news_item.get('feed_name', '?'))
            title = news_item.get('title', 'No title')
            symbol_text = f"{symbol}: "

            font_sym = self._fonts.get('symbol')
            font_hl = self._fonts.get('headline')

            draw_tmp = ImageDraw.Draw(Image.new('RGB', (1, 1)))
            sym_bbox = draw_tmp.textbbox((0, 0), symbol_text, font=font_sym)
            hl_bbox = draw_tmp.textbbox((0, 0), title, font=font_hl)

            sym_w = sym_bbox[2] - sym_bbox[0]
            hl_w = hl_bbox[2] - hl_bbox[0]
            text_h = max(sym_bbox[3] - sym_bbox[1], hl_bbox[3] - hl_bbox[1])
            gap = 4
            total_w = sym_w + gap + hl_w

            img = Image.new('RGB', (total_w, self.display_height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            y = max(0, (self.display_height - text_h) // 2)
            draw.text((0, y), symbol_text, fill=self.symbol_color, font=font_sym)
            draw.text((sym_w + gap, y), title, fill=self.text_color, font=font_hl)
            return img
        except Exception as e:
            self.logger.warning(f"Error rendering news item: {e}")
            return None

    def _display_no_news(self):
        """Display message when no news is available."""
        img = Image.new('RGB', (self.display_manager.matrix.width,
                               self.display_manager.matrix.height),
                       (0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text((5, 12), "No Stock News", fill=(150, 150, 150))

        self.display_manager.image = img.copy()
        self.display_manager.update_display()

    def _display_error(self, message: str):
        """Display error message."""
        img = Image.new('RGB', (self.display_manager.matrix.width,
                               self.display_manager.matrix.height),
                       (0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text((5, 12), message, fill=(255, 0, 0))

        self.display_manager.image = img.copy()
        self.display_manager.update_display()

    def get_display_duration(self) -> float:
        """Get display duration from config."""
        return self.display_duration

    def get_info(self) -> Dict[str, Any]:
        """Return plugin info for web UI."""
        info = super().get_info()
        info.update({
            'total_news_items': len(self.all_news_items),
            'stock_symbols': self.feeds_config.get('stock_symbols', []),
            'custom_feeds': list(self.feeds_config.get('custom_feeds', {}).keys()),
            'last_update': self.last_update,
            'display_duration': self.display_duration,
            'scroll_speed': self.scroll_speed,
            'max_headlines_per_symbol': self.max_headlines_per_symbol,
            'headlines_per_rotation': self.headlines_per_rotation,
            'font_size': self.font_size,
            'text_color': self.text_color,
            'symbol_color': self.symbol_color,
            'separator_color': self.separator_color
        })
        return info

    def cleanup(self) -> None:
        """Cleanup resources."""
        self.all_news_items = []
        self.current_news_items = []
        self.logger.info("Stock news ticker plugin cleaned up")
