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


# ─────────────────────────────────────────────────────────────────────
# Opportunity rate vs tradeable alert rate
#
# An alert bot's edge and its delivered alert count are different
# numbers. The backtester used to `return` early whenever a simulated
# position was open, which silently removed those setups from the sample
# and made the strategy look rarer than it was.
# ─────────────────────────────────────────────────────────────────────
import strategy_config as cfg
from tests.helpers import make_candles


def _run_with(candles, **kw):
    return backtest.BacktestRun(candles, **kw)


def _fake_scored(direction="BUY", entry=2000.0, score=60):
    return {
        "direction": direction, "pattern": "GOLDEN_TRIO", "score": score,
        "entry_price": entry, "stop_loss": entry - 25.0,
        "tp1": entry + 25.0, "tp2": entry + 50.0, "tp3": entry + 100.0,
        "risk": 25.0, "breakdown": [("gt_setup", 30)], "htf_bias": "BULL",
        "zlsma_status": "aligned", "tier": "WATCH", "aplus_eligible": False,
    }


def test_suppressed_opportunities_are_recorded_not_dropped():
    candles = make_candles(300, start_price=2000.0, step=0.5, noise=1.0)
    run = _run_with(candles)
    run._record("WATCH", _fake_scored(), 100, suppressed=None)
    run._record("WATCH", _fake_scored(), 101, suppressed="open_position")
    assert len(run.signals) == 2
    assert run.signals[0]["tradeable"] is True
    assert run.signals[1]["tradeable"] is False
    assert run.signals[1]["suppressed_reason"] == "open_position"


def test_suppressed_opportunities_are_still_simulated():
    """A suppressed setup still has an outcome -- the market did what it
    did whether or not the bot was free to alert on it. Dropping them
    biases the sample toward whatever the position gate happened to let
    through."""
    candles = make_candles(300, start_price=2000.0, step=0.5, noise=1.0)
    run = _run_with(candles)
    run._record("WATCH", _fake_scored(), 100, suppressed="cooldown: same-direction")
    row = run.signals[0]
    assert set(row["outcomes"]) == {"ideal", "realistic", "conservative"}
    assert "outcome" in row["outcomes"]["realistic"]


def test_only_delivered_alerts_occupy_the_simulated_position():
    candles = make_candles(300, start_price=2000.0, step=0.5, noise=1.0)
    run = _run_with(candles)
    run._record("WATCH", _fake_scored(), 100, suppressed="open_position")
    assert run.blocked_until_bar == -1      # a suppressed row changes nothing
    run._record("WATCH", _fake_scored(), 100, suppressed=None)
    assert run.blocked_until_bar > 100


def test_signal_rows_carry_the_mtf_audit_trail_for_every_timeframe():
    candles = make_candles(300, start_price=2000.0, step=0.5, noise=1.0)
    run = _run_with(candles)
    run._record("WATCH", _fake_scored(), 200, suppressed=None)
    row = run.signals[0]
    for key in ("m15", "h1", "h4", "m5", "m1"):
        assert f"{key}_last_t" in row
        assert f"{key}_bars" in row


# ─── finer-timeframe slicing must not look ahead ─────────────────────

def test_finer_timeframe_slice_never_passes_the_m15_bar_close():
    m15 = make_candles(50, start_price=2000.0, step=1.0, interval_minutes=15)
    m5 = make_candles(150, start_price=2000.0, step=0.3, interval_minutes=5)
    run = _run_with(m15, m5=m5)
    import pandas as pd
    for i in (10, 20, 30):
        market = run._build_market(i)
        if not market["m5"]:
            continue
        m15_close = pd.to_datetime(m15[i]["t"], utc=True) + pd.Timedelta(minutes=15)
        last_m5 = pd.to_datetime(market["m5"][-1]["t"], utc=True)
        assert last_m5 < m15_close


def test_m5_and_m1_are_empty_when_not_supplied():
    """Without real finer candles the MTF layer must see nothing rather
    than something synthesised from M15 -- a fabricated M5 series would
    be look-ahead by construction."""
    m15 = make_candles(50, start_price=2000.0, step=1.0)
    run = _run_with(m15)
    market = run._build_market(30)
    assert market["m5"] == []
    assert market["m1"] == []


def test_target_mode_is_threaded_into_candidate_discovery():
    m15 = make_candles(50, start_price=2000.0, step=1.0)
    assert _run_with(m15, target_mode="ATR").target_mode == "ATR"
    assert _run_with(m15).target_mode == cfg.TARGET_MODE


# ─────────────────────────────────────────────────────────────────────
# Spread is a cost, not a looser fill trigger.
#
# Regression: entry_eff was used for BOTH the cost basis and the fill
# trigger, so a wider spread made a BUY fill whenever the bar dipped to
# entry + spread. A wider spread produced an EASIER fill, and the three
# cost regimes stopped sharing a trade set -- which is precisely the
# property that lets them isolate the cost of spread.
# ─────────────────────────────────────────────────────────────────────

def _ohlc_bar(o, h, l, c):
    return {"t": "2025-01-01T00:00:00+00:00", "o": o, "h": h, "l": l, "c": c, "v": 1}


def _scored(direction="BUY", entry=2000.0):
    sign = 1.0 if direction == "BUY" else -1.0
    return {"direction": direction, "entry_price": entry,
            "stop_loss": entry - sign * 25.0,
            "tp1": entry + sign * 25.0, "tp2": entry + sign * 50.0,
            "tp3": entry + sign * 100.0, "risk": 25.0}


def test_wider_spread_never_creates_a_fill_that_a_tighter_spread_missed():
    """A bar that stops just short of the entry must not fill under any
    spread. Previously it filled under the conservative regime only."""
    # BUY at 2000; the bar's low only reaches 2000.80 -- never touches entry.
    forward = [_ohlc_bar(2002.0, 2003.0, 2000.80, 2001.0)] * 8
    for spread in (0.0, 0.75, 1.50):
        outcome, _r, _x = backtest.simulate_execution(
            _scored("BUY"), forward, spread_price=spread)
        assert outcome == "no_fill_expired", f"filled at spread {spread}"


def test_sell_side_mirrors_the_no_fill_behaviour():
    forward = [_ohlc_bar(1998.0, 1999.20, 1997.0, 1998.0)] * 8
    for spread in (0.0, 0.75, 1.50):
        outcome, _r, _x = backtest.simulate_execution(
            _scored("SELL"), forward, spread_price=spread)
        assert outcome == "no_fill_expired", f"filled at spread {spread}"


def test_all_cost_regimes_fill_on_exactly_the_same_bars():
    """The regimes must differ only in R, never in which trades exist --
    otherwise they aren't measuring the cost of spread, they're measuring
    two different samples."""
    forward = ([_ohlc_bar(2001.0, 2002.0, 1999.5, 2000.5)]        # touches entry
               + [_ohlc_bar(2001.0, 2027.0, 2000.0, 2026.0)] * 6)  # runs to TP1
    fills = []
    for _name, spread in backtest.COST_REGIMES:
        outcome, r, exit_off = backtest.simulate_execution(
            _scored("BUY"), forward, spread_price=spread)
        fills.append((outcome, exit_off, r))
    outcomes = {f[0] for f in fills}
    exits = {f[1] for f in fills}
    assert len(outcomes) == 1, f"regimes diverged on outcome: {fills}"
    assert len(exits) == 1, f"regimes diverged on fill/exit timing: {fills}"


def test_spread_still_reduces_the_realised_r():
    """The fix must not quietly stop charging for spread."""
    forward = ([_ohlc_bar(2001.0, 2002.0, 1999.5, 2000.5)]
               + [_ohlc_bar(2001.0, 2027.0, 2000.0, 2026.0)] * 6)
    _o, r_ideal, _ = backtest.simulate_execution(_scored("BUY"), forward, spread_price=0.0)
    _o, r_wide, _ = backtest.simulate_execution(_scored("BUY"), forward, spread_price=1.50)
    assert r_wide < r_ideal
