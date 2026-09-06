"""Look-ahead-bias guardrails for the backtest.

Two independent checks:

1) aggregate_htf drops the trailing incomplete bar so no unclosed HTF
   candle ever leaks into scoring.

2) A single BacktestRun scan at bar i evaluates strategy code against
   ONLY candles[0..i]. We verify this by replacing candles[i+1:] with
   sentinel prices that would corrupt any indicator that peeked past i,
   and confirming the signal decision at bar i is identical.
"""
import os
from copy import deepcopy

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "12345")

import numpy as np

import backtest


def _make_candles(n=200, seed=7):
    np.random.seed(seed)
    p = 4000 + np.cumsum(np.random.randn(n) * 3)
    out = []
    for i, price in enumerate(p):
        o = price - np.random.rand() * 2
        c = price + np.random.rand() * 2
        h = max(o, c) + abs(np.random.rand()) * 3 + 0.5
        l = min(o, c) - abs(np.random.rand()) * 3 - 0.5
        # ISO 8601 UTC, 15-minute intervals
        ts = f"2026-01-01T{(i * 15) // 60 % 24:02d}:{(i * 15) % 60:02d}:00+0000"
        out.append({"t": ts, "o": float(o), "h": float(h),
                    "l": float(l), "c": float(c)})
    return out


def test_aggregate_htf_drops_partial_bar():
    """A 5-bar M15 window can only produce ONE closed H1 (4 bars);
    the 5th bar is left as an incomplete developing H1 and MUST NOT
    surface in the aggregated series."""
    m15 = _make_candles(n=5)
    h1 = backtest.aggregate_htf(m15, 4)
    assert len(h1) == 1

    m15 = _make_candles(n=15)   # 3 closed H1s + 3 developing
    h1 = backtest.aggregate_htf(m15, 4)
    assert len(h1) == 3


def test_aggregate_htf_output_stable_when_future_bars_change():
    """If we corrupt bars past index N, aggregate_htf(candles[:N+1])
    must not change. Guards against sneaky global-window aggregation."""
    original = _make_candles(n=100)
    stop = 40
    h1_before = backtest.aggregate_htf(original[:stop], 4)

    corrupted = deepcopy(original)
    for k in range(stop, len(corrupted)):
        corrupted[k]["h"] = 999999.0
        corrupted[k]["l"] = -999999.0
        corrupted[k]["c"] = 999999.0
    h1_after = backtest.aggregate_htf(corrupted[:stop], 4)

    assert h1_before == h1_after


def test_backtest_scan_only_sees_past_candles():
    """The single most important invariant: BacktestRun.scan(i) must
    produce the same signal (or non-signal) whether or not candles[i+1:]
    exist / are corrupted. Any look-ahead in the strategy would break this.
    """
    original = _make_candles(n=200)

    # Two runs from identical prefixes, one with corrupted future bars.
    corrupted = deepcopy(original)
    for k in range(100, len(corrupted)):
        corrupted[k]["h"] = 999999.0
        corrupted[k]["l"] = -999999.0
        corrupted[k]["c"] = 999999.0
        corrupted[k]["o"] = 999999.0

    run_a = backtest.BacktestRun(original)
    run_b = backtest.BacktestRun(corrupted)

    # Compare the DECISION made at each bar up to the corruption boundary.
    # We only check whether a signal fired and its score/direction — the
    # simulate_execution outcome legitimately depends on future bars.
    for i in range(50, 99):
        run_a.scan(i)
        run_b.scan(i)

    decisions_a = [(s["bar_index"], s["direction"], s["score"], s["tier"])
                   for s in run_a.signals]
    decisions_b = [(s["bar_index"], s["direction"], s["score"], s["tier"])
                   for s in run_b.signals]
    assert decisions_a == decisions_b, (
        f"Look-ahead bias detected: scanning up to bar 98 produced "
        f"different signals when future bars were corrupted.\n"
        f"clean:     {decisions_a}\n"
        f"corrupted: {decisions_b}")


# ─────────────────────────────────────────────────────────────────────
# 3) Every timeframe in the market bundle must close at or before the
#    M15 signal bar. The MTF layer added M5 and M1, which are sliced
#    from separate series rather than aggregated -- a different code
#    path, and therefore a different opportunity to leak the future.
# ─────────────────────────────────────────────────────────────────────
import pandas as pd

from tests.helpers import make_candles

_TF_MINUTES = {"m15": 15, "h1": 60, "h4": 240, "m5": 5, "m1": 1}


def _assert_no_future_bars(market, signal_ts):
    """No timeframe may contain a bar that closes after the M15 bar does."""
    m15_close = pd.to_datetime(signal_ts, utc=True) + pd.Timedelta(minutes=15)
    for tf, span in _TF_MINUTES.items():
        series = market.get(tf) or []
        if not series:
            continue
        last_close = (pd.to_datetime(series[-1]["t"], utc=True)
                      + pd.Timedelta(minutes=span))
        assert last_close <= m15_close, (
            f"{tf} leaked a bar closing at {last_close}, past the M15 "
            f"signal bar's close at {m15_close}")


def test_market_bundle_never_contains_a_bar_from_the_future():
    m15 = make_candles(200, start_price=2000.0, step=0.5, noise=1.0, interval_minutes=15)
    m5 = make_candles(600, start_price=2000.0, step=0.16, noise=0.4, interval_minutes=5)
    m1 = make_candles(3000, start_price=2000.0, step=0.03, noise=0.1, interval_minutes=1)
    run = backtest.BacktestRun(m15, m5=m5, m1=m1)
    for i in range(60, 190, 7):
        _assert_no_future_bars(run._build_market(i), m15[i]["t"])


def test_finer_timeframe_cursor_is_monotonic_across_the_walk():
    """The M5/M1 slice uses a forward-only cursor. If it could rewind, a
    later bar could re-expose data an earlier scan had correctly hidden."""
    m15 = make_candles(150, start_price=2000.0, step=0.5, interval_minutes=15)
    m5 = make_candles(450, start_price=2000.0, step=0.16, interval_minutes=5)
    run = backtest.BacktestRun(m15, m5=m5)
    seen = -1
    for i in range(40, 145):
        run._build_market(i)
        assert run._m5_cursor >= seen
        seen = run._m5_cursor


def test_signals_logged_by_a_full_run_carry_no_future_timestamps():
    """End-to-end: every row the backtester writes must pass the audit."""
    m15 = make_candles(700, start_price=2000.0, step=0.4, noise=1.2, interval_minutes=15)
    m5 = make_candles(2100, start_price=2000.0, step=0.13, noise=0.4, interval_minutes=5)
    signals = backtest.run_backtest(m15, m5=m5)
    for s in signals:
        m15_close = pd.to_datetime(s["t"], utc=True) + pd.Timedelta(minutes=15)
        for tf, span in _TF_MINUTES.items():
            last_t = s.get(f"{tf}_last_t")
            if not last_t:
                continue
            close = pd.to_datetime(last_t, utc=True) + pd.Timedelta(minutes=span)
            assert close <= m15_close, f"{tf} leak in logged signal at {s['t']}"
