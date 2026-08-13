"""Sanity tests for backtest.py -- verifies the simulator's outcome logic
against fabricated forward-candle sequences."""
import backtest


def _bar(h, l, o=None, c=None):
    o = o if o is not None else (h + l) / 2
    c = c if c is not None else o
    return {"t": "", "o": o, "h": h, "l": l, "c": c, "v": None}


def _candidate(direction="BUY", entry=100.0, stop=95.0, tp1=105.0, tp2=110.0, tp3=115.0, risk=None):
    risk = risk if risk is not None else abs(entry - stop)
    return {
        "direction": direction, "entry_price": entry, "stop_loss": stop,
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "risk": risk, "quality": 20,
    }


def test_no_fill_expired_when_price_never_reaches_entry():
    # BUY entry at 100, but price stays above 105 for the whole entry window.
    forward = [_bar(h=110, l=106) for _ in range(20)]
    outcome, r = backtest.simulate_trade(_candidate(direction="BUY", entry=100.0), forward)
    assert outcome == "no_fill_expired"
    assert r == 0.0


def test_stop_before_tp1_records_full_r_loss():
    # BUY: fill immediately, then drop straight through stop.
    forward = [_bar(h=101, l=99)] + [_bar(h=99, l=94) for _ in range(5)]
    outcome, r = backtest.simulate_trade(_candidate(direction="BUY"), forward)
    assert outcome == "stop_before_tp1"
    assert r == -1.0


def test_tp3_runner_complete_gives_expected_r_multiple():
    # BUY: fill, ramp up through TP1, TP2, TP3 across sequential bars.
    forward = [
        _bar(h=101, l=99),               # fill
        _bar(h=106, l=100),              # TP1 (105) hit -> stop moves to breakeven
        _bar(h=111, l=105),              # TP2 (110) hit -> stop moves to TP1
        _bar(h=116, l=110),              # TP3 (115) hit -> runner complete
    ]
    outcome, r = backtest.simulate_trade(_candidate(direction="BUY"), forward)
    assert outcome == "tp3_runner_complete"
    # 0.5 * (105-100)/5 + 0.3 * (110-100)/5 + 0.2 * (115-100)/5 = 0.5 + 0.6 + 0.6 = 1.7
    assert abs(r - 1.7) < 1e-6


def test_breakeven_after_tp1_when_stop_hit_at_breakeven():
    # BUY: fill, hit TP1, then pull back to breakeven stop.
    forward = [
        _bar(h=101, l=99),               # fill
        _bar(h=106, l=100),              # TP1 hit
        _bar(h=101, l=99),               # pulls back to entry (=breakeven stop)
    ]
    outcome, r = backtest.simulate_trade(_candidate(direction="BUY"), forward)
    assert outcome == "breakeven_after_tp1"
    # 0.5 * 1R (from TP1) + 0.5 * 0R (breakeven stop) = 0.5
    assert abs(r - 0.5) < 1e-6


def test_runner_stopped_after_tp2_records_locked_r():
    forward = [
        _bar(h=101, l=99),               # fill
        _bar(h=106, l=100),              # TP1
        _bar(h=111, l=105),              # TP2 -> stop moves to TP1 (105)
        _bar(h=110, l=104),              # pulls back below TP1 -> runner stopped at 105
    ]
    outcome, r = backtest.simulate_trade(_candidate(direction="BUY"), forward)
    assert outcome == "runner_stopped"
    # 0.5 * 1R + 0.3 * 2R + 0.2 * 1R = 0.5 + 0.6 + 0.2 = 1.3
    assert abs(r - 1.3) < 1e-6


def test_short_direction_mirror_stop_before_tp1():
    # SELL entry at 100, stop at 105.
    cand = _candidate(direction="SELL", entry=100.0, stop=105.0, tp1=95.0, tp2=90.0, tp3=85.0)
    forward = [_bar(h=101, l=99)] + [_bar(h=106, l=100) for _ in range(5)]
    outcome, r = backtest.simulate_trade(cand, forward)
    assert outcome == "stop_before_tp1"
    assert r == -1.0


def test_aggregate_htf_folds_four_m15_into_one_h1():
    m15 = [
        {"t": "2026-01-01T00:00:00", "o": 100, "h": 102, "l": 99,  "c": 101, "v": None},
        {"t": "2026-01-01T00:15:00", "o": 101, "h": 104, "l": 100, "c": 103, "v": None},
        {"t": "2026-01-01T00:30:00", "o": 103, "h": 105, "l": 102, "c": 104, "v": None},
        {"t": "2026-01-01T00:45:00", "o": 104, "h": 106, "l": 103, "c": 105, "v": None},
    ]
    h1 = backtest.aggregate_htf(m15, 4)
    assert len(h1) == 1
    assert h1[0]["o"] == 100
    assert h1[0]["c"] == 105
    assert h1[0]["h"] == 106
    assert h1[0]["l"] == 99
