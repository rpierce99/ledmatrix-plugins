#!/usr/bin/env python3
"""
Regression test: a `_live` mode with no live games must skip (display() returns
False) so the rotation moves on, instead of holding the slot with a blank frame.

Run standalone from the plugin directory:

    cd plugins/hockey-scoreboard
    python test_live_skip_empty.py

The test stubs the sibling manager modules so `manager.py` imports without the
LEDMatrix core, builds the plugin without running __init__, and drives display()
with a stubbed per-league live manager. `_display_league_mode` is stubbed to
return True (simulating "I rendered content") so that, before the fix, an empty
live mode would wrongly report content. After the fix the empty live mode is
skipped before it ever reaches the renderer.

No external test framework — exits non-zero on the first failure.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))


def _stub(name: str, attrs) -> None:
    mod = types.ModuleType(name)
    for attr in attrs:
        setattr(mod, attr, object)
    sys.modules[name] = mod


_stub("scroll_display", ["ScrollDisplayManager"])
_stub("nhl_managers", ["NHLLiveManager", "NHLRecentManager", "NHLUpcomingManager"])
_stub(
    "ncaam_hockey_managers",
    ["NCAAMHockeyLiveManager", "NCAAMHockeyRecentManager", "NCAAMHockeyUpcomingManager"],
)
_stub(
    "ncaaw_hockey_managers",
    ["NCAAWHockeyLiveManager", "NCAAWHockeyRecentManager", "NCAAWHockeyUpcomingManager"],
)

import manager  # noqa: E402


class _NullLogger:
    def __getattr__(self, _name):
        return lambda *a, **k: None


def _make_plugin(live_games):
    """Build a plugin instance whose 'nhl' league live manager has `live_games`."""
    plugin = object.__new__(manager.HockeyScoreboardPlugin)
    plugin.is_enabled = True
    plugin.logger = _NullLogger()
    plugin._league_registry = {"nhl": {"enabled": True}}
    plugin.config = {"nhl": {"display_modes": {"live": True, "recent": True}}}
    plugin._current_active_display_mode = None

    live_mgr = types.SimpleNamespace(live_games=live_games, favorite_teams=[])
    plugin._get_league_manager_for_mode = (
        lambda league, mode_type: live_mgr if mode_type == "live" else object()
    )
    # Simulate the renderer always claiming it drew content. Without the fix this
    # makes an empty live mode return True (the blank-frame bug).
    plugin._display_league_mode = lambda league, mode_type, force_clear=False: True
    return plugin


_LIVE_GAME = {"is_final": False, "home_abbr": "SEA", "away_abbr": "VAN"}


def test_empty_live_mode_skips() -> None:
    plugin = _make_plugin(live_games=[])
    result = plugin.display("nhl_live")
    assert result is False, f"empty nhl_live should skip (return False), got {result!r}"
    print("  [ok] empty live mode returns False (skips)")


def test_live_mode_with_game_shows() -> None:
    plugin = _make_plugin(live_games=[_LIVE_GAME])
    result = plugin.display("nhl_live")
    assert result is True, f"nhl_live with a live game should show, got {result!r}"
    print("  [ok] live mode with a game returns True (shows)")


def test_recent_mode_unaffected() -> None:
    # recent mode must not be gated by live-content; it always has content to show.
    plugin = _make_plugin(live_games=[])
    result = plugin.display("nhl_recent")
    assert result is True, f"nhl_recent should be unaffected by the guard, got {result!r}"
    print("  [ok] recent mode unaffected by the live-skip guard")


def main() -> int:
    for test in (
        test_empty_live_mode_skips,
        test_live_mode_with_game_shows,
        test_recent_mode_unaffected,
    ):
        try:
            test()
        except AssertionError as exc:
            print(f"  [FAIL] {test.__name__}: {exc}")
            return 1
    print("All live-skip tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
