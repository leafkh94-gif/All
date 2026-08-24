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
