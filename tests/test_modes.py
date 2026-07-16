import strategy_config as cfg
from strategy import modes


def test_standard_mode_matches_current_cfg_constants():
    assert modes.STANDARD.entry_timeframe == "15min"
    assert modes.STANDARD.scan_interval_minutes == cfg.SCAN_INTERVAL_MINUTES
    assert modes.STANDARD.watch_min_score == cfg.WATCH_MIN_SCORE
    assert modes.STANDARD.aplus_min_score == cfg.APLUS_MIN_SCORE
    assert modes.STANDARD.watch_collapse_score == cfg.WATCH_COLLAPSE_SCORE
    assert modes.STANDARD.watch_expiry_minutes == cfg.WATCH_EXPIRY_HOURS * 60
    assert modes.STANDARD.watch_update_interval_minutes == cfg.WATCH_UPDATE_INTERVAL_MINUTES
    assert modes.STANDARD.entry_expiry_minutes == cfg.ENTRY_EXPIRY_HOURS * 60
    assert modes.STANDARD.atr_low_percentile == cfg.ATR_LOW_PERCENTILE
    assert modes.STANDARD.atr_high_percentile == cfg.ATR_HIGH_PERCENTILE
    assert modes.STANDARD.session_cutoff_enabled is True


def test_loose_mode_has_lower_thresholds_than_standard():
    assert modes.LOOSE.watch_min_score < modes.STANDARD.watch_min_score
    assert modes.LOOSE.aplus_min_score < modes.STANDARD.aplus_min_score
    assert modes.LOOSE.watch_collapse_score < modes.STANDARD.watch_collapse_score
    assert modes.LOOSE.entry_timeframe == modes.STANDARD.entry_timeframe
    assert modes.LOOSE.scan_interval_minutes == modes.STANDARD.scan_interval_minutes


def test_fast_mode_has_shorter_timeframe_and_faster_cadence():
    assert modes.FAST.entry_timeframe == "5min"
    assert modes.FAST.scan_interval_minutes < modes.STANDARD.scan_interval_minutes
    assert modes.FAST.watch_expiry_minutes < modes.STANDARD.watch_expiry_minutes
    assert modes.FAST.watch_update_interval_minutes < modes.STANDARD.watch_update_interval_minutes
    assert modes.FAST.entry_expiry_minutes < modes.STANDARD.entry_expiry_minutes


def test_get_mode_falls_back_to_standard_for_unknown_name():
    assert modes.get_mode("bogus") is modes.STANDARD
    assert modes.get_mode("fast") is modes.FAST


def test_swing_mode_uses_hourly_entries_and_disables_session_cutoff():
    assert modes.SWING.entry_timeframe == "1h"
    assert modes.SWING.scan_interval_minutes == 60
    assert modes.SWING.session_cutoff_enabled is False
    assert modes.SWING.watch_expiry_minutes > modes.STANDARD.watch_expiry_minutes
    assert modes.SWING.watch_update_interval_minutes > modes.STANDARD.watch_update_interval_minutes
    assert modes.SWING.entry_expiry_minutes > modes.STANDARD.entry_expiry_minutes
    assert modes.MODES["swing"] is modes.SWING


def test_loose_and_fast_still_have_session_cutoff_enabled():
    assert modes.LOOSE.session_cutoff_enabled is True
    assert modes.FAST.session_cutoff_enabled is True
