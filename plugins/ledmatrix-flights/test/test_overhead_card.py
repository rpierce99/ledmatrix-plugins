#!/usr/bin/env python3
"""
Regression tests for the redesigned overhead card (FlightRenderer.render_overhead).

Covers:
  - Heading-arrow geometry (`_heading_arrow_points`) for the four cardinals and
    invalid input.
  - The card renders without error on the 128x32 hardware panel and on a large
    panel, producing a non-blank image.
  - The two bugs the redesign fixes can't recur:
      * the duplicate airline tag ("UAL360 UAL") — the callsign is drawn once and
        the airline ICAO is never appended to it.
      * the raw degree glyph in the heading ("HDG:227°") — no "°" is ever drawn
        (heading goes through format_track / an arrow instead).

Run with the core venv:
    .venv/bin/python plugins/ledmatrix-flights/test/test_overhead_card.py
"""

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

import PIL.ImageDraw as _ID  # noqa: E402
from renderer import FlightRenderer  # noqa: E402


class _FakeMatrix:
    def __init__(self, w, h):
        self.width = w
        self.height = h


class _FakeDM:
    def __init__(self, w, h):
        self.matrix = _FakeMatrix(w, h)
        self.image = None

    def update_display(self):
        pass


def make_renderer(w=128, h=32, config=None):
    return FlightRenderer(_FakeDM(w, h), {}, config or {})


def _aircraft(**over):
    ac = {
        "callsign": "UAL360",
        "airline_icao": "UAL",
        "altitude": 2725,
        "speed": 163,
        "distance_miles": 0.86,
        "heading": 227,
        "origin": "DEN",
        "destination": "SEA",
        "aircraft_type": "B738",
        "color": (0, 200, 255),
    }
    ac.update(over)
    return ac


def _capture_text(fn):
    """Run fn() while recording every string passed to ImageDraw.text."""
    captured = []
    orig = _ID.ImageDraw.text

    def rec(self, xy, text="", *a, **k):
        captured.append(text)
        return orig(self, xy, text, *a, **k)

    _ID.ImageDraw.text = rec
    try:
        fn()
    finally:
        _ID.ImageDraw.text = orig
    return captured


# ---------------------------------------------------------------------------
# Heading-arrow geometry
# ---------------------------------------------------------------------------

def test_arrow_points_north_points_up():
    # 0deg = North = up: tip is above the center (smaller y).
    tip, *_ = FlightRenderer._heading_arrow_points(10, 10, 5, 0)
    assert tip[1] < 10 and abs(tip[0] - 10) < 0.01, tip


def test_arrow_points_east_points_right():
    tip, *_ = FlightRenderer._heading_arrow_points(10, 10, 5, 90)
    assert tip[0] > 10 and abs(tip[1] - 10) < 0.01, tip


def test_arrow_points_south_points_down():
    tip, *_ = FlightRenderer._heading_arrow_points(10, 10, 5, 180)
    assert tip[1] > 10 and abs(tip[0] - 10) < 0.01, tip


def test_arrow_points_west_points_left():
    tip, *_ = FlightRenderer._heading_arrow_points(10, 10, 5, 270)
    assert tip[0] < 10 and abs(tip[1] - 10) < 0.01, tip


def test_arrow_points_intercardinals():
    # Tip must land in the correct quadrant for the four intercardinals.
    cases = {45: (1, -1), 135: (1, 1), 225: (-1, 1), 315: (-1, -1)}  # (sign x, sign y)
    for hdg, (sx, sy) in cases.items():
        tip, *_ = FlightRenderer._heading_arrow_points(0, 0, 16, hdg)
        assert (tip[0] > 0) == (sx > 0) and (tip[1] > 0) == (sy > 0), (hdg, tip)


def test_arrow_points_invalid_heading_empty():
    assert FlightRenderer._heading_arrow_points(10, 10, 5, None) == []
    assert FlightRenderer._heading_arrow_points(10, 10, 5, "") == []
    assert FlightRenderer._heading_arrow_points(10, 10, 5, "abc") == []


# ---------------------------------------------------------------------------
# Card rendering
# ---------------------------------------------------------------------------

def test_renders_128x32_nonblank():
    r = make_renderer(128, 32)
    img = r.render_overhead_image(_aircraft(), progress=0.99, delay="")
    assert img.size == (128, 32)
    assert img.getbbox() is not None, "card rendered fully blank"


def test_renders_large_nonblank():
    r = make_renderer(256, 64)
    img = r.render_overhead_image(_aircraft(), progress=0.5, delay="DELAYED 12m")
    assert img.size == (256, 64)
    assert img.getbbox() is not None


def test_renders_without_heading_or_route():
    # GA tail number: no airline logo, no route, no heading badge.
    r = make_renderer(128, 32)
    img = r.render_overhead_image(
        _aircraft(callsign="N12345", airline_icao="", origin="", destination="",
                  heading=None)
    )
    assert img.getbbox() is not None


def test_empty_aircraft_is_safe():
    r = make_renderer(128, 32)
    img = r.render_overhead_image(None)
    assert img.size == (128, 32)


# ---------------------------------------------------------------------------
# Regressions: no doubled airline, no degree glyph
# ---------------------------------------------------------------------------

def test_callsign_not_doubled():
    r = make_renderer(128, 32)
    texts = _capture_text(lambda: r.render_overhead_image(_aircraft(), progress=0.99))
    joined = " ".join(texts)
    # The old layout drew "UAL360 UAL"; the redesign must not.
    assert "UAL360 UAL" not in joined, joined
    # And the callsign itself is still shown.
    assert any(t == "UAL360" for t in texts), texts


def test_no_degree_glyph_drawn():
    # Heading is shown as an arrow, never as "227°" text — the old bug rendered a
    # degree glyph the PressStart font can't draw.
    r = make_renderer(128, 32)
    texts = _capture_text(lambda: r.render_overhead_image(_aircraft(), progress=0.99))
    assert all("°" not in t for t in texts), texts
    # The badge conveys distance as text alongside the heading arrow.
    assert any("mi" in t for t in texts), texts


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # pragma: no cover
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return failures == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
