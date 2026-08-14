import strategy_config as cfg
from strategy import modes


def test_standard_mode_matches_current_cfg_constants():
    assert modes.STANDARD.entry_timeframe == "5min"
    assert modes.STANDARD.scan_interval_minutes == cfg.SCAN_INTERVAL_MINUTES
    assert modes.STANDARD.watch_min_score == cfg.WATCH_MIN_SCORE
    assert modes.STANDARD.aplus_min_score == cfg.APLUS_MIN_SCORE
    assert modes.STANDARD.watch_collapse_score == cfg.WATCH_COLLAPSE_SCORE
    assert modes.STANDARD.watch_expiry_minutes == cfg.WATCH_EXPIRY_HOURS * 60
    assert modes.STANDARD.watch_update_interval_minutes == cfg.WATCH_UPDATE_INTERVAL_MINUTES
    assert modes.STANDARD.entry_expiry_minutes == cfg.ENTRY_EXPIRY_HOURS * 60
    assert modes.STANDARD.session_cutoff_enabled is True


def test_only_standard_mode_exists():
    assert set(modes.MODES) == {"standard"}
    assert modes.DEFAULT_MODE == "standard"


def test_get_mode_falls_back_to_standard():
    assert modes.get_mode("bogus") is modes.STANDARD
    assert modes.get_mode("standard") is modes.STANDARD
