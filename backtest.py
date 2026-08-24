"""Replay the LIVE Golden Trio + SMC pipeline over historical M15 candles.

This backtester intentionally uses the exact same code paths as the live
bot (scoring_strategy.find_candidate + score_candidate, main_alerts
cooldown_blocks_alert + record_alert_for_cooldown, PendingAPlusStore-
style A+ confirmation, tracker gating) so live and backtest can't drift.

Read-only: does not modify state files, does not send Telegram, does not
touch the running bot. Purely a research replay.

Usage:
    python backtest.py --candles path/to/XAUUSD_M15.csv
    python backtest.py --candles path/to/XAUUSD_M15.csv --json out.json

CSV must have columns t, o, h, l, c (v optional). Timestamps ISO 8601 UTC.
Bars must be chronological.

Every fired signal is simulated under three cost regimes (ideal,
realistic $0.75 spread, conservative $1.50 spread) so results aren't
optimistic.

Every fired signal's log row includes the M15 / H1 / H4 last-bar
timestamps and bar counts so a follow-up look-ahead-bias audit can
verify no future data leaked in.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

import pandas as pd

import main_alerts as ma
import scoring_strategy as strat
import strategy_config as cfg
from strategy import modes


# ─────────────────────────────────────────────────────────────────────
# Data loading + HTF aggregation (unchanged; deterministic, no lookahead)
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
    """Aggregate an M15 window into H1 (factor=4) or H4 (factor=16) bars.
    ONLY consumes m15_candles[:len(m15_candles)] -- no future access.
    Drops the trailing partial bar so we never expose an unclosed HTF."""
    out = []
    for i in range(0, len(m15_candles), factor):
        chunk = m15_candles[i:i + factor]
        if len(chunk) < factor:
            break  # skip developing bar
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
# Execution simulator — mirrors OpenTradeTracker's 3-tier logic.
# Handles fill delay + spread cost + partial exits.
# ─────────────────────────────────────────────────────────────────────
_ENTRY_EXPIRY_BARS = 6           # 90 min pending-order cap
_HOLD_EXPIRY_BARS = 4 * 96       # up to 4 days of M15


def _r_at(price, entry, risk, is_buy):
    return (price - entry) / risk if is_buy else (entry - price) / risk


def simulate_execution(scored, forward, spread_price=0.0):
    """
    Return (outcome_tag, r_weighted, exit_offset_bars).
    exit_offset_bars is bars from signal-time to trade termination (or
    None if never filled). Used to gate the next alert.

    Spread is charged on the entry only (fill worse by `spread_price`).
    Same-bar SL+TP → SL wins (conservative).
    """
    direction = scored["direction"]
    is_buy = direction == "BUY"
    entry = scored["entry_price"]
    stop = scored["stop_loss"]
    tp1 = scored["tp1"]
    tp2 = scored["tp2"]
    tp3 = scored["tp3"]
    if abs(entry - stop) <= 0:
        return "invalid_risk", 0.0, None

    # Slip entry by spread: BUY fills at ask (higher), SELL at bid (lower).
    entry_eff = entry + spread_price if is_buy else entry - spread_price
    risk_eff = abs(entry_eff - stop)
    if risk_eff <= 0:
        return "spread_erased_risk", 0.0, None

    fill_idx = None
    for i, c in enumerate(forward[:_ENTRY_EXPIRY_BARS]):
        if (is_buy and c["l"] <= entry_eff) or (not is_buy and c["h"] >= entry_eff):
            fill_idx = i
            break
    if fill_idx is None:
        return "no_fill_expired", 0.0, len(forward[:_ENTRY_EXPIRY_BARS])

    curr_stop = stop
    tp1_hit = False
    tp2_hit = False
    locked_r = 0.0

    for j, c in enumerate(forward[fill_idx + 1: fill_idx + 1 + _HOLD_EXPIRY_BARS]):
        stop_hit = (is_buy and c["l"] <= curr_stop) or (not is_buy and c["h"] >= curr_stop)
        tp1_touch = not tp1_hit and ((is_buy and c["h"] >= tp1) or (not is_buy and c["l"] <= tp1))
        tp2_touch = tp1_hit and not tp2_hit and ((is_buy and c["h"] >= tp2) or (not is_buy and c["l"] <= tp2))
        tp3_touch = tp2_hit and ((is_buy and c["h"] >= tp3) or (not is_buy and c["l"] <= tp3))
        exit_bar = fill_idx + 1 + j

        if stop_hit:
            r_at_stop = _r_at(curr_stop, entry_eff, risk_eff, is_buy)
            if not tp1_hit:
                return "stop_before_tp1", -1.0, exit_bar
            if not tp2_hit:
                return "breakeven_after_tp1", locked_r + 0.5 * r_at_stop, exit_bar
            return "runner_stopped", locked_r + 0.2 * r_at_stop, exit_bar

        if tp1_touch:
            tp1_hit = True
            locked_r += 0.5 * _r_at(tp1, entry_eff, risk_eff, is_buy)
            curr_stop = entry_eff
            continue
        if tp2_touch:
            tp2_hit = True
            locked_r += 0.3 * _r_at(tp2, entry_eff, risk_eff, is_buy)
            curr_stop = tp1
            continue
        if tp3_touch:
            locked_r += 0.2 * _r_at(tp3, entry_eff, risk_eff, is_buy)
            return "tp3_runner_complete", locked_r, exit_bar

    outcome = "time_expired_after_tp1" if tp1_hit else "time_expired_no_fill_progress"
    end_offset = fill_idx + max(1, len(forward[fill_idx + 1: fill_idx + 1 + _HOLD_EXPIRY_BARS]))
    return outcome, locked_r, end_offset


def simulate_trade(candidate, forward):
    """Back-compat wrapper: 2-arg call, returns (outcome, r). No spread."""
    outcome, r, _ = simulate_execution(candidate, forward, spread_price=0.0)
    return outcome, r


# ─────────────────────────────────────────────────────────────────────
# Backtest driver — mirrors main_alerts.run scan flow, in-memory state
# ─────────────────────────────────────────────────────────────────────
COST_REGIMES = [
    ("ideal", 0.0),
    ("realistic", 0.75),          # ~mid-day gold spread
    ("conservative", 1.50),       # session-open / news spread
]


class BacktestRun:
    """One walk over the candle series. Fires signals under the exact live
    pipeline; simulates trade outcomes under three cost regimes; logs each
    signal with the MTF state it was evaluated against."""

    def __init__(self, candles, mode=None):
        self.candles = candles
        self.mode = mode or modes.STANDARD

        # State that live persists to disk; here in-memory per-run.
        self.main_state = {}
        self.pending_a_plus = None          # (scored_dict, added_at_index)
        self.blocked_until_bar = -1         # no new signal while i < this
        self.signals = []

    # ── helpers ──────────────────────────────────────────────────────
    def _now(self, i):
        return pd.to_datetime(self.candles[i]["t"], utc=True).to_pydatetime()

    def _build_market(self, i):
        window = self.candles[: i + 1]
        return {
            "entry": window,
            "m15": window,
            "h1": aggregate_htf(window, 4),
            "h4": aggregate_htf(window, 16),
        }

    def _tick_pending(self, i):
        """Live analog: evaluate_pending_confirmations. Exactly one bar
        window: if pending was set at bar j, the confirmation bar is j+1."""
        if self.pending_a_plus is None:
            return
        scored, added_at = self.pending_a_plus
        if i <= added_at:
            return  # not yet — pending was just set this same bar
        # We only ever confirm on the immediately-following bar.
        if i == added_at + 1:
            last_closed = self.candles[i]
            direction = scored["direction"]
            if strat.confirmation_closed_in_direction(last_closed, direction):
                # Rescore on the new window; if still A+, fire.
                market = self._build_market(i)
                candidate = strat.find_candidate(market["entry"])
                if candidate and candidate["direction"] == direction:
                    now = self._now(i)
                    rescored = strat.score_candidate(
                        "XAUUSD", "COMMODITY", candidate, market, now, None)
                    if rescored and rescored["score"] >= self.mode.aplus_min_score:
                        self._fire("A+", rescored, i)
        # Whether or not it fired, the pending window has passed.
        self.pending_a_plus = None

    # ── scan ─────────────────────────────────────────────────────────
    def scan(self, i):
        self._tick_pending(i)

        if i < self.blocked_until_bar:
            return  # a prior alert's trade is still live in the simulation

        market = self._build_market(i)
        now = self._now(i)

        candidate = strat.find_candidate(market["entry"])
        if candidate is None:
            return

        scored = strat.score_candidate(
            "XAUUSD", "COMMODITY", candidate, market, now, None)
        if not scored or scored["tier"] == "NONE":
            return

        blocked, _ = ma.cooldown_blocks_alert(
            self.main_state, "XAUUSD", scored["direction"], scored["entry_price"], now)
        if blocked:
            return

        if scored.get("aplus_eligible"):
            # A+ waits one bar for confirmation, mirrors PendingAPlusStore.
            if self.pending_a_plus is not None:
                return
            self.pending_a_plus = (scored, i)
            ma.record_alert_for_cooldown(
                self.main_state, "XAUUSD", scored["direction"], scored["entry_price"], now)
            return

        if scored["score"] >= self.mode.watch_min_score:
            self._fire("WATCH", scored, i)
            ma.record_alert_for_cooldown(
                self.main_state, "XAUUSD", scored["direction"], scored["entry_price"], now)

    # ── fire + simulate ──────────────────────────────────────────────
    def _fire(self, tier, scored, i):
        forward = self.candles[i + 1: i + 1 + _ENTRY_EXPIRY_BARS + _HOLD_EXPIRY_BARS]
        outcomes = {}
        for name, spread in COST_REGIMES:
            outcome, r, exit_offset = simulate_execution(scored, forward, spread_price=spread)
            outcomes[name] = {"outcome": outcome, "r": round(r, 3),
                              "exit_bar_offset": exit_offset}

        # Block further alerts until the simulated trade (under realistic
        # cost) is done -- roughly mirrors live blocking-while-in-trade.
        realistic_exit = outcomes["realistic"]["exit_bar_offset"] or _ENTRY_EXPIRY_BARS
        self.blocked_until_bar = i + 1 + realistic_exit

        # MTF audit fields — exact bar timestamps + counts each timeframe saw
        market = self._build_market(i)
        m15 = market["m15"]
        h1 = market["h1"]
        h4 = market["h4"]

        # Breakdown as a name→pts map for easy score-band analysis
        breakdown_map = {tag: pts for tag, pts in scored.get("breakdown", [])}

        self.signals.append({
            "bar_index": i,
            "t": self.candles[i]["t"],
            "tier": tier,
            "pattern": scored["pattern"],
            "direction": scored["direction"],
            "score": scored["score"],
            "breakdown": breakdown_map,
            "h4_bias": scored.get("htf_bias"),
            "zlsma_status": scored.get("zlsma_status"),
            # Look-ahead audit trail
            "m15_last_t": m15[-1]["t"] if m15 else None,
            "m15_bars": len(m15),
            "h1_last_t": h1[-1]["t"] if h1 else None,
            "h1_bars": len(h1),
            "h4_last_t": h4[-1]["t"] if h4 else None,
            "h4_bars": len(h4),
            # Execution
            "entry_price": scored["entry_price"],
            "stop_loss": scored["stop_loss"],
            "tp1": scored["tp1"], "tp2": scored["tp2"], "tp3": scored["tp3"],
            "risk": scored["risk"],
            "outcomes": outcomes,
        })


def run_backtest(candles, mode=None):
    """Walk every bar, emit signals through the live pipeline."""
    run = BacktestRun(candles, mode=mode)
    warmup = max(
        cfg.GT_ZLSMA_PERIOD * 2 + cfg.GT_ZLSMA_SLOPE_LOOKBACK + 5,
        16 * 30,   # need ≥30 H4 bars for htf_bias
    )
    for i in range(warmup, len(candles) - 1):
        run.scan(i)
    return run.signals


# ─────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────
def _stats(subset, cost="realistic"):
    if not subset:
        return None
    filled = [s for s in subset if not s["outcomes"][cost]["outcome"].startswith("no_fill")]
    if not filled:
        return {"n": 0, "wr": 0.0, "avg_r": 0.0, "total_r": 0.0}
    rs = [s["outcomes"][cost]["r"] for s in filled]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    losing_streak = 0
    max_losing_streak = 0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if r < 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0

    return {
        "n": len(filled),
        "wins": len(wins),
        "wr": len(wins) / len(filled),
        "avg_r": sum(rs) / len(rs),
        "total_r": sum(rs),
        "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
        "max_dd_r": round(max_dd, 2),
        "max_losing_streak": max_losing_streak,
    }


def _fmt(s):
    if not s:
        return "n=0"
    return (f"n={s['n']} wr={s['wr']*100:.0f}% avg={s['avg_r']:+.2f}R "
            f"total={s['total_r']:+.1f}R pf={s['profit_factor']} "
            f"dd={s['max_dd_r']}R streak={s['max_losing_streak']}")


def print_summary(signals):
    print(f"\nTotal signals fired: {len(signals)}")
    if not signals:
        return
    ts_first = signals[0]["t"]
    ts_last = signals[-1]["t"]
    print(f"Span: {ts_first} → {ts_last}")

    tiers = Counter(s["tier"] for s in signals)
    print(f"By tier: {dict(tiers)}")

    for cost, _ in COST_REGIMES:
        overall = _stats(signals, cost=cost)
        print(f"\n[{cost}] overall: {_fmt(overall)}")

    # Split: pattern
    print("\nBy detector (realistic cost):")
    for pat in sorted({s["pattern"] for s in signals}):
        subset = [s for s in signals if s["pattern"] == pat]
        print(f"  {pat}: {_fmt(_stats(subset))}")

    # Split: tier
    print("\nBy tier (realistic cost):")
    for tier in ["WATCH", "A+"]:
        subset = [s for s in signals if s["tier"] == tier]
        print(f"  {tier}: {_fmt(_stats(subset))}")

    # Split: direction
    print("\nBy direction (realistic cost):")
    for direction in ["BUY", "SELL"]:
        subset = [s for s in signals if s["direction"] == direction]
        print(f"  {direction}: {_fmt(_stats(subset))}")

    # Split: score band
    print("\nBy score band (realistic cost):")
    for lo, hi in [(0, 44), (45, 49), (50, 54), (55, 59), (60, 64),
                    (65, 69), (70, 74), (75, 79), (80, 84), (85, 100)]:
        subset = [s for s in signals if lo <= s["score"] <= hi]
        if subset:
            print(f"  {lo:>2}-{hi:>2}: {_fmt(_stats(subset))}")

    # Split: session
    print("\nBy session UTC (realistic cost):")
    def _hour_of(ts):
        return int(str(ts)[11:13])
    sessions = {
        "asian (00-07)": lambda h: 0 <= h < 7,
        "london (07-13)": lambda h: 7 <= h < 13,
        "overlap (13-16)": lambda h: 13 <= h < 16,
        "ny (16-21)": lambda h: 16 <= h < 21,
        "after (21-24)": lambda h: 21 <= h < 24,
    }
    for name, pred in sessions.items():
        subset = [s for s in signals if pred(_hour_of(s["t"]))]
        print(f"  {name}: {_fmt(_stats(subset))}")

    # Outcome distribution (realistic)
    print("\nOutcome distribution (realistic):")
    counts = Counter(s["outcomes"]["realistic"]["outcome"] for s in signals)
    for outcome, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {outcome}: {n}")

    # TP hit rates (of filled trades under realistic cost)
    filled = [s for s in signals if not s["outcomes"]["realistic"]["outcome"].startswith("no_fill")]
    if filled:
        tp1_hit = sum(1 for s in filled if s["outcomes"]["realistic"]["outcome"] not in
                       ("stop_before_tp1", "time_expired_no_fill_progress", "invalid_risk", "spread_erased_risk"))
        tp2_hit = sum(1 for s in filled if s["outcomes"]["realistic"]["outcome"] in
                       ("breakeven_after_tp1", "runner_stopped", "tp3_runner_complete", "time_expired_after_tp1"))
        # Note: breakeven_after_tp1 means we reached TP1 then hit BE stop -- TP2 NOT hit
        tp2_hit = sum(1 for s in filled if s["outcomes"]["realistic"]["outcome"] in
                       ("runner_stopped", "tp3_runner_complete"))
        tp3_hit = sum(1 for s in filled if s["outcomes"]["realistic"]["outcome"] == "tp3_runner_complete")
        print(f"\nTP hit rates (of {len(filled)} filled): "
              f"TP1={tp1_hit/len(filled)*100:.0f}% "
              f"TP2={tp2_hit/len(filled)*100:.0f}% "
              f"TP3={tp3_hit/len(filled)*100:.0f}%")


def main():
    parser = argparse.ArgumentParser(description="Replay live pipeline over historical candles.")
    parser.add_argument("--candles", required=True,
                        help="CSV path with columns t,o,h,l,c[,v] chronological")
    parser.add_argument("--json", help="Optional per-signal log JSON path")
    args = parser.parse_args()

    # Tests / CI / local runs shouldn't need real Telegram creds.
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "backtest-noop")
    os.environ.setdefault("TELEGRAM_CHAT_ID", "0")

    candles = load_candles(args.candles)
    print(f"Loaded {len(candles)} candles from {args.candles}")
    signals = run_backtest(candles)
    print_summary(signals)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(signals, f, indent=2, default=str)
        print(f"\nPer-signal log written to {args.json}")


if __name__ == "__main__":
    main()
