import datetime as dt

from strategy import modes
from strategy.watch_tracker import WatchTracker
import strategy_config as cfg


def _now():
    return dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)


def _scored(instrument="US500", direction="BUY", score=68):
    return {"instrument": instrument, "direction": direction, "score": score,
            "entry_price": 5420.0, "stop_loss": 5398.0, "tp1": 5464.0, "tp2": 5508.0}


def _tracker(tmp_path, rescore_value, mode=None):
    messages = []
    rescorer = lambda direction, instrument, now_utc: (
        {"score": rescore_value, "instrument": instrument, "direction": direction,
         "entry_price": 5420.0, "stop_loss": 5398.0, "tp1": 5464.0, "tp2": 5508.0}
        if rescore_value is not None else None)
    tracker = WatchTracker(
        rescorer=rescorer, notifier=lambda text: messages.append(text),
        aplus_formatter=lambda scored: "A+ BODY",
        path=str(tmp_path / "watches.json"), mode=mode)
    return tracker, messages


def test_add_and_has_active(tmp_path):
    tracker, _ = _tracker(tmp_path, rescore_value=68)
    assert not tracker.has_active("US500")
    tracker.add(_scored(), _now())
    assert tracker.has_active("US500")


def test_expiry_removes_silently_no_message(tmp_path):
    tracker, messages = _tracker(tmp_path, rescore_value=68)
    tracker.add(_scored(), _now())
    later = _now() + dt.timedelta(hours=cfg.WATCH_EXPIRY_HOURS, minutes=1)
    tracker.evaluate_all(later)
    assert not tracker.has_active("US500")
    assert messages == []


def test_upgrade_to_aplus_sends_message_and_removes(tmp_path):
    tracker, messages = _tracker(tmp_path, rescore_value=80)
    tracker.add(_scored(score=68), _now())
    tracker.evaluate_all(_now() + dt.timedelta(minutes=15))
    assert not tracker.has_active("US500")
    assert any("WATCH → A+" in m for m in messages)


def test_collapse_sends_quiet_cancel_and_removes(tmp_path):
    tracker, messages = _tracker(tmp_path, rescore_value=40)
    tracker.add(_scored(score=68), _now())
    tracker.evaluate_all(_now() + dt.timedelta(minutes=15))
    assert not tracker.has_active("US500")
    assert any("watch closed" in m for m in messages)


def test_still_monitoring_sends_update_after_45_min(tmp_path):
    tracker, messages = _tracker(tmp_path, rescore_value=70)
    tracker.add(_scored(score=68), _now())
    tracker.evaluate_all(_now() + dt.timedelta(minutes=46))
    assert tracker.has_active("US500")
    assert any("WATCH Update" in m for m in messages)


def test_still_monitoring_no_update_before_45_min(tmp_path):
    tracker, messages = _tracker(tmp_path, rescore_value=70)
    tracker.add(_scored(score=68), _now())
    tracker.evaluate_all(_now() + dt.timedelta(minutes=20))
    assert tracker.has_active("US500")
    assert messages == []


def test_pattern_gone_treated_as_collapse(tmp_path):
    tracker, messages = _tracker(tmp_path, rescore_value=None)
    tracker.add(_scored(score=68), _now())
    tracker.evaluate_all(_now() + dt.timedelta(minutes=15))
    assert not tracker.has_active("US500")
    assert any("watch closed" in m for m in messages)


def test_mode_shorter_expiry_fast(tmp_path):
    """Fast mode's 80-min expiry must close a WATCH that would still be
    active under the default 240-min (4h) expiry."""
    tracker, messages = _tracker(tmp_path, rescore_value=70, mode=modes.FAST)
    tracker.add(_scored(score=68), _now())
    tracker.evaluate_all(_now() + dt.timedelta(minutes=81))
    assert not tracker.has_active("US500")
    assert messages == []  # silent expiry, same as the default-mode expiry test


def test_mode_default_expiry_unaffected_at_81_minutes(tmp_path):
    tracker, _ = _tracker(tmp_path, rescore_value=70)
    tracker.add(_scored(score=68), _now())
    tracker.evaluate_all(_now() + dt.timedelta(minutes=81))
    assert tracker.has_active("US500")


def test_mode_aplus_threshold_loose(tmp_path):
    """A rescore of 70 must upgrade to A+ under loose mode (aplus_min=68) but
    stay in monitoring under the default mode (aplus_min=75)."""
    default_tracker, default_messages = _tracker(tmp_path, rescore_value=70)
    default_tracker.add(_scored(score=68), _now())
    default_tracker.evaluate_all(_now() + dt.timedelta(minutes=15))
    assert default_tracker.has_active("US500")
    assert default_messages == []

    loose_tracker, loose_messages = _tracker(tmp_path, rescore_value=70, mode=modes.LOOSE)
    loose_tracker.add(_scored(score=68), _now())
    loose_tracker.evaluate_all(_now() + dt.timedelta(minutes=15))
    assert not loose_tracker.has_active("US500")
    assert any("WATCH → A+" in m for m in loose_messages)


def test_mode_collapse_threshold_loose(tmp_path):
    """A rescore of 50 must collapse under the default mode (collapse=55) but
    stay in monitoring under loose mode (collapse=48)."""
    default_tracker, default_messages = _tracker(tmp_path, rescore_value=50)
    default_tracker.add(_scored(score=68), _now())
    default_tracker.evaluate_all(_now() + dt.timedelta(minutes=15))
    assert not default_tracker.has_active("US500")
    assert any("watch closed" in m for m in default_messages)

    loose_tracker, loose_messages = _tracker(tmp_path, rescore_value=50, mode=modes.LOOSE)
    loose_tracker.add(_scored(score=68), _now())
    loose_tracker.evaluate_all(_now() + dt.timedelta(minutes=15))
    assert loose_tracker.has_active("US500")
    assert loose_messages == []


def test_on_upgrade_callback_invoked(tmp_path):
    calls = []
    rescorer = lambda direction, instrument, now_utc: {
        "score": 80, "instrument": instrument, "direction": direction,
        "entry_price": 5420.0, "stop_loss": 5398.0, "tp1": 5464.0, "tp2": 5508.0}
    tracker = WatchTracker(
        rescorer=rescorer, notifier=lambda text: None,
        aplus_formatter=lambda scored: "A+ BODY",
        on_upgrade=lambda scored, now_utc: calls.append(scored["instrument"]),
        path=str(tmp_path / "watches.json"))
    tracker.add(_scored(score=68), _now())
    tracker.evaluate_all(_now() + dt.timedelta(minutes=15))
    assert calls == ["US500"]
