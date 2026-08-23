"""Replay Golden Trio signals over historical M15 candles.

Read-only: does not modify state, does not send Telegram alerts, does not
touch the running bot. Purely a hypothetical replay to answer two things
the live bot's scoring cannot:

  1. Is the H4-bias hard block earning its keep? (What would win rate look
     like for signals it VETOES?)
  2. Does score correlate with outcome? (Higher-score signals should win
     more; if they don't, the score is decorative.)

Usage:
    python backtest.py --candles path/to/XAUUSD_M15.csv
    python backtest.py --candles path/to/XAUUSD_M15.csv --json out.json

The CSV must have columns t, o, h, l, c (v optional). Timestamps must be
ISO 8601, UTC, and bars must be chronological.
"""
import argparse
import json
from collections import defaultdict

import pandas as pd

import market_sessions
import scoring_indicators as ind
import scoring_strategy as strat
import strategy_config as cfg
from strategy.golden_trio import find_golden_trio_candidate


# ─────────────────────────────────────────────────────────────────────
# Data loading + HTF aggregation
# ─────────────────────────────────────────────────────────────────────
def load_candles(csv_path):
    df = pd.read_csv(csv_path)
    required = {"t", "o", "h", "l", "c"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")
    if "v" not in df.columns:
        df["v"] = None
    return df.to_dict(orient="records")


def aggregate_htf(m15_candles, factor):
    """Aggregate M15 candles into H1 (factor=4) or H4 (factor=16) bars."""
    out = []
    for i in range(0, len(m15_candles), factor):
        chunk = m15_candles[i:i + factor]
        if len(chunk) < factor:
            break
        out.append({
            "t": chunk[0]["t"],
            "o": chunk[0]["o"],
            "h": max(c["h"] for c in chunk),
            "l": min(c["l"] for c in chunk),
            "c": chunk[-1]["c"],
            "v": None,
        })
    return out


# ─────────────────────────────────────────────────────────────────────
# 3-tier tracker replay (mirrors main_alerts.OpenTradeTracker semantics)
# ─────────────────────────────────────────────────────────────────────
_ENTRY_EXPIRY_BARS = 6            # 6 * 15min = 90min (matches PENDING_ORDER_MAX_MINUTES)
_HOLD_EXPIRY_BARS = 4 * 16        # 4 days of M15 bars = long enough for TP3 or stop


def _r_at(price, entry, risk, is_buy):
    return (price - entry) / risk if is_buy else (entry - price) / risk


def simulate_trade(candidate, forward_candles):
    """Return (outcome, r_multiple). Deterministic; SL always wins ties."""
    direction = candidate["direction"]
    is_buy = direction == "BUY"
    entry = candidate["entry_price"]
    stop = candidate["stop_loss"]
    tp1 = candidate["tp1"]
    tp2 = candidate["tp2"]
    tp3 = candidate["tp3"]
    risk = candidate["risk"]
    if risk <= 0:
        return "invalid_risk", 0.0

    # Phase 1: wait for the pending limit to fill.
    fill_idx = None
    for i, c in enumerate(forward_candles[:_ENTRY_EXPIRY_BARS]):
        if (is_buy and c["l"] <= entry) or (not is_buy and c["h"] >= entry):
            fill_idx = i
            break
    if fill_idx is None:
        return "no_fill_expired", 0.0

    # Phase 2: manage the 3-tier exit.
    curr_stop = stop
    tp1_hit = False
    tp2_hit = False
    locked_r = 0.0

    for c in forward_candles[fill_idx + 1: fill_idx + 1 + _HOLD_EXPIRY_BARS]:
        stop_hit = (is_buy and c["l"] <= curr_stop) or (not is_buy and c["h"] >= curr_stop)
        tp1_touch = not tp1_hit and ((is_buy and c["h"] >= tp1) or (not is_buy and c["l"] <= tp1))
        tp2_touch = tp1_hit and not tp2_hit and ((is_buy and c["h"] >= tp2) or (not is_buy and c["l"] <= tp2))
        tp3_touch = tp2_hit and ((is_buy and c["h"] >= tp3) or (not is_buy and c["l"] <= tp3))

        # Conservative: if same bar hits both a TP and the stop, treat SL as first.
        if stop_hit:
            r_at_stop = _r_at(curr_stop, entry, risk, is_buy)
            if not tp1_hit:
                return "stop_before_tp1", -1.0
            if not tp2_hit:
                return "breakeven_after_tp1", locked_r + 0.5 * r_at_stop
            return "runner_stopped", locked_r + 0.2 * r_at_stop

        if tp1_touch:
            tp1_hit = True
            locked_r += 0.5 * _r_at(tp1, entry, risk, is_buy)
            curr_stop = entry
            continue
        if tp2_touch:
            tp2_hit = True
            locked_r += 0.3 * _r_at(tp2, entry, risk, is_buy)
            curr_stop = tp1
            continue
        if tp3_touch:
            locked_r += 0.2 * _r_at(tp3, entry, risk, is_buy)
            return "tp3_runner_complete", locked_r

    return ("time_expired_after_tp1" if tp1_hit else "time_expired_no_fill_progress", locked_r)


# ─────────────────────────────────────────────────────────────────────
# Main replay loop
# ─────────────────────────────────────────────────────────────────────
class _StubLevelStore:
    def get_daily_levels(self, _):
        return None

    def get_weekly_levels(self, _):
        return None


def run_backtest(candles):
    """Walk every bar; on each Golden Trio signal, score and simulate."""
    signals = []
    warmup = cfg.GT_ZLSMA_PERIOD * 2 + cfg.GT_ZLSMA_SLOPE_LOOKBACK + 5
    for i in range(warmup, len(candles) - 1):
        window = candles[: i + 1]
        candidate = find_golden_trio_candidate(window)
        if not candidate:
            continue

        h4 = aggregate_htf(window, 16)
        bias = strat.htf_bias(h4)
        would_veto = ((bias == "BULL" and candidate["direction"] == "SELL")
                      or (bias == "BEAR" and candidate["direction"] == "BUY"))

        # Score using the live scorer, but bypass the block so we get a
        # score for VETOED signals too (to answer "would they have won").
        market = {"entry": window, "m15": window, "h1": aggregate_htf(window, 4), "h4": h4}
        now = pd.to_datetime(window[-1]["t"], utc=True).to_pydatetime()
        # Force-run scorer even in opposition by temporarily aligning bias
        original_bias_fn = strat.htf_bias
        strat.htf_bias = lambda _c, **_kw: candidate["direction"] == "BUY" and "BULL" or "BEAR"
        try:
            scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market, now, _StubLevelStore())
        finally:
            strat.htf_bias = original_bias_fn

        forward = candles[i + 1:]
        outcome, r = simulate_trade(candidate, forward)

        signals.append({
            "bar": i,
            "t": window[-1]["t"],
            "direction": candidate["direction"],
            "score": scored["score"] if scored else None,
            "quality": candidate.get("rsi_quality", 0) + candidate.get("turtle_quality", 0),
            "rsi": round(candidate.get("rsi", 0.0), 1),
            "h4_bias": bias,
            "would_veto": would_veto,
            "outcome": outcome,
            "r_multiple": round(r, 3),
        })
    return signals


# ─────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────
def _stats(subset):
    if not subset:
        return None
    filled = [s for s in subset if not s["outcome"].startswith("no_fill")]
    if not filled:
        return {"n": 0, "wins": 0, "wr": 0.0, "avg_r": 0.0, "total_r": 0.0}
    wins = [s for s in filled if s["r_multiple"] > 0]
    total_r = sum(s["r_multiple"] for s in filled)
    return {
        "n": len(filled),
        "wins": len(wins),
        "wr": len(wins) / len(filled),
        "avg_r": total_r / len(filled),
        "total_r": total_r,
    }


def print_summary(signals):
    print(f"\nTotal signals fired: {len(signals)}")
    if not signals:
        return
    overall = _stats(signals)
    print(f"Filled: {overall['n']} / {len(signals)} "
          f"({overall['n'] / len(signals) * 100:.0f}%)")
    print(f"Overall: {overall['wr'] * 100:.0f}% win rate, "
          f"{overall['avg_r']:+.2f}R avg, {overall['total_r']:+.1f}R total")

    print("\nBy H4 hard block:")
    for veto, label in [(False, "H4 aligned (bot actually took)"),
                         (True, "H4 opposed (bot BLOCKED these)")]:
        s = _stats([x for x in signals if x["would_veto"] == veto])
        if s:
            print(f"  {label}: n={s['n']}, wr={s['wr'] * 100:.0f}%, avg={s['avg_r']:+.2f}R, total={s['total_r']:+.1f}R")

    print("\nBy score band (aligned signals only):")
    aligned = [s for s in signals if not s["would_veto"]]
    for lo, hi, label in [(0, 61, "no_alert"), (62, 74, "WATCH"), (75, 84, "A+"), (85, 100, "A+ strong")]:
        bucket = _stats([s for s in aligned if s["score"] is not None and lo <= s["score"] <= hi])
        if bucket and bucket["n"]:
            print(f"  {label} ({lo}-{hi}): n={bucket['n']}, wr={bucket['wr'] * 100:.0f}%, avg={bucket['avg_r']:+.2f}R")

    print("\nOutcome distribution:")
    counts = defaultdict(int)
    for s in signals:
        counts[s["outcome"]] += 1
    for outcome, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {outcome}: {n}")


def main():
    parser = argparse.ArgumentParser(description="Replay Golden Trio over historical candles.")
    parser.add_argument("--candles", required=True,
                        help="CSV path with columns t,o,h,l,c[,v] and chronological rows")
    parser.add_argument("--json", help="Optional path to dump the per-signal log as JSON")
    args = parser.parse_args()

    candles = load_candles(args.candles)
    print(f"Loaded {len(candles)} candles from {args.candles}")
    signals = run_backtest(candles)
    print_summary(signals)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(signals, f, indent=2)
        print(f"\nPer-signal log written to {args.json}")


if __name__ == "__main__":
    main()
