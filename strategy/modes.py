"""
Trading mode config. Only STANDARD survives now that the bot is gold-only on
one strategy. Kept as a NamedTuple + registry so run_forever.py's /mode
command handling and every mode= call site continues to work unmodified.
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
    session_cutoff_enabled: bool = True


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
    session_cutoff_enabled=True,
)

MODES = {"standard": STANDARD}
DEFAULT_MODE = "standard"


def get_mode(name):
    return MODES.get(name, STANDARD)
