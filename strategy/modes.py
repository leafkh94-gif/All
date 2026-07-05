"""
Trading modes — user-selectable profiles layered on top of strategy_config.py.

STANDARD is derived from strategy_config so it can never silently drift from
the bot's default (and only) behavior before modes existed. LOOSE and FAST
are variants built via ._replace(), documented in the plan this implements.
"""
from typing import NamedTuple

import strategy_config as cfg


class ModeConfig(NamedTuple):
    name: str
    entry_timeframe: str
    scan_interval_minutes: int
    watch_min_score: int
    aplus_min_score: int
    watch_collapse_score: int
    watch_expiry_minutes: int
    watch_update_interval_minutes: int
    entry_expiry_minutes: int
    atr_low_percentile: float
    atr_high_percentile: float


STANDARD = ModeConfig(
    name="standard",
    entry_timeframe="15min",
    scan_interval_minutes=cfg.SCAN_INTERVAL_MINUTES,
    watch_min_score=cfg.WATCH_MIN_SCORE,
    aplus_min_score=cfg.APLUS_MIN_SCORE,
    watch_collapse_score=cfg.WATCH_COLLAPSE_SCORE,
    watch_expiry_minutes=cfg.WATCH_EXPIRY_HOURS * 60,
    watch_update_interval_minutes=cfg.WATCH_UPDATE_INTERVAL_MINUTES,
    entry_expiry_minutes=cfg.ENTRY_EXPIRY_HOURS * 60,
    atr_low_percentile=cfg.ATR_LOW_PERCENTILE,
    atr_high_percentile=cfg.ATR_HIGH_PERCENTILE,
)

# Same 15-min pacing as standard; only the alert gates move (relative gap
# sizes preserved: WATCH-to-A+ and WATCH-to-collapse are both still 13/7).
LOOSE = STANDARD._replace(
    name="loose",
    watch_min_score=55,
    aplus_min_score=68,
    watch_collapse_score=48,
    atr_low_percentile=5,
    atr_high_percentile=85,
)

# Shorter entry timeframe + faster cadence; every wall-clock timing field is
# scaled by the same 5/15 ratio as the timeframe change, which preserves each
# lifecycle's underlying candle count (16 candles to WATCH expiry, 3 candles
# between updates, 8 candles to entry expiry) rather than the raw hour figure.
FAST = STANDARD._replace(
    name="fast",
    entry_timeframe="5min",
    scan_interval_minutes=5,
    watch_expiry_minutes=80,
    watch_update_interval_minutes=15,
    entry_expiry_minutes=40,
)

MODES = {"standard": STANDARD, "loose": LOOSE, "fast": FAST}
DEFAULT_MODE = "standard"


def get_mode(name):
    return MODES.get(name, STANDARD)
