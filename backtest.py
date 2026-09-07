"""Replay the LIVE Golden Trio + SMC + MTF pipeline over historical candles.

This backtester intentionally uses the exact same code paths as the live
bot (scoring_strategy.find_candidate + score_candidate, strategy.mtf,
main_alerts cooldown_blocks_alert + record_alert_for_cooldown,
PendingAPlusStore-style A+ confirmation) so live and backtest can't drift.

Read-only: does not modify state files, does not send Telegram, does not
touch the running bot. Purely a research replay.

Usage:
    python backtest.py --candles XAUUSD_M15.csv
    python backtest.py --candles XAUUSD_M15.csv --m5 XAUUSD_M5.csv --m1 XAUUSD_M1.csv
    python backtest.py --candles XAUUSD_M15.csv --target-mode ATR
    python backtest.py --candles XAUUSD_M15.csv --json out.json

CSV must have columns t, o, h, l, c (v optional). Timestamps ISO 8601 UTC.
Bars must be chronological.

TWO METRICS, NOT ONE
────────────────────
This is an alert bot, not a one-position execution engine, so a single
"number of trades" figure is misleading. Every bar that produces a
tier-qualifying signal is recorded as an **opportunity** and simulated,
including the ones a live position or a cooldown would have suppressed.
The report then gives:

  - Signal opportunity rate: every qualifying setup the strategy found.
    This is the honest measure of the *strategy's* edge and the sample
    the score calibration should be built from.
  - Tradeable alert rate: what survives the cooldown and one-position-
    at-a-time gates. This is what a user would actually have received.

Conflating them lets a position gate silently shrink the sample and
makes the strategy look rarer (or better) than it is.

TARGET MODES
────────────
--target-mode {FIXED,ATR} runs the same candles through the fixed dollar
ladder or the ATR-scaled one, so the "is $25 the right stop in every
volatility regime?" question is answered by comparison rather than by
assumption. Run both and diff the expectancy.

MTF INPUTS
──────────
H1 and H4 are aggregated from M15 with the trailing partial bar dropped,
so no unclosed higher-timeframe candle is ever visible. M5 and M1 cannot
be synthesised from M15; pass real M5/M1 CSVs with --m5/--m1 to exercise
those layers. Without them, strategy.mtf scores those timeframes as
neutral (exactly 0), which is why the MTF contributions are zero-centred
-- an M15-only run and a full-MTF run stay on the same score scale
instead of differing by a constant offset. Each signal row records
mtf_available so a report can never silently conflate the two.

Every fired signal is simulated under three cost regimes (ideal,
realistic $0.75 spread, conservative $1.50 spread) so results aren't
optimistic.

Every signal's log row includes the M15 / H1 / H4 (and M5 / M1 when
supplied) last-bar timestamps and bar counts so a follow-up look-ahead-
bias audit can verify no future data leaked in.
"""
import argparse
import json
import os
from bisect import bisect_right
from collections import Counter

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


def precompute_htf(candles, factor):
    """Aggregate the WHOLE series once, recording where each HTF bar ends.

    Returns (bars, end_idx) where end_idx[k] is the index of the last M15
    bar inside HTF bar k. A scan at bar i may use exactly the bars whose
    end_idx <= i, which reproduces aggregate_htf(candles[:i+1], factor)
    bar-for-bar -- same fixed chunk grid anchored at index 0, same dropped
    trailing partial -- but costs O(n) for the whole run instead of O(n)
    per scan.

    Anchoring matters: aggregating a *sliding* window would move the chunk
    boundaries every bar, so the "same" H4 candle would keep changing
    shape as the run advanced.
    """
    bars, ends = [], []
    for start in range(0, len(candles) - factor + 1, factor):
        chunk = candles[start:start + factor]
        bars.append({
            "t": chunk[0]["t"],
            "o": chunk[0]["o"],
            "h": max(c["h"] for c in chunk),
            "l": min(c["l"] for c in chunk),
            "c": chunk[-1]["c"],
            "v": None,
        })
        ends.append(start + factor - 1)
    return bars, ends


# ─────────────────────────────────────────────────────────────────────
# Execution simulator — mirrors OpenTradeTracker's 3-tier logic.
# Handles fill delay + spread cost + partial exits.
# ─────────────────────────────────────────────────────────────────────
_ENTRY_EXPIRY_BARS = 6           # 90 min pending-order cap
_HOLD_EXPIRY_BARS = 96           # one trading day of M15.
                                 # Was 4*96. On realistic volatility a 3xATR
                                 # trade resolves in ~17 bars at the median and
                                 # 97% resolve inside one day, so a 4-day window
                                 # bought almost no extra resolution while
                                 # holding the one-position gate shut for days.
                                 # A scalp that hasn't resolved in a day is dead.


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

    # Spread is a COST, not a looser trigger.
    #
    # The feed is bid-quoted, so the fill triggers when the bid touches
    # the alerted entry level; what the spread changes is the price you
    # actually paid (BUY fills at the ask, SELL at the bid), which is
    # entry_eff below and the basis for every R calculation after it.
    #
    # Triggering on entry_eff instead -- as this did previously -- made a
    # BUY fill whenever the bar dipped to entry + spread, i.e. a WIDER
    # spread produced an EASIER fill. That is backwards, and it also
    # meant the three cost regimes stopped evaluating the same set of
    # trades: the conservative regime picked up marginal fills the ideal
    # regime never entered, so the regimes could no longer isolate the
    # cost of spread. On a 573-signal run the filled counts diverged
    # 543 / 555 / 563 across ideal / realistic / conservative.
    #
    # Trigger on the raw entry; charge the spread on the fill price only.
    # All three regimes now share one trade set and differ solely by cost.
    entry_eff = entry + spread_price if is_buy else entry - spread_price
    risk_eff = abs(entry_eff - stop)
    if risk_eff <= 0:
        return "spread_erased_risk", 0.0, None

    fill_idx = None
    for i, c in enumerate(forward[:_ENTRY_EXPIRY_BARS]):
        if (is_buy and c["l"] <= entry) or (not is_buy and c["h"] >= entry):
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


def index_by_time(candles):
    """Map ISO timestamp -> position, for slicing a finer timeframe to the
    same instant as an M15 bar without ever seeing past it."""
    return {str(c["t"]): i for i, c in enumerate(candles)}


class BacktestRun:
    """One walk over the candle series.

    Records EVERY tier-qualifying signal as an opportunity, tags whether
    it would have been suppressed live (open simulated position, or
    cooldown), and simulates the outcome regardless. That separation is
    the whole point: an alert bot's edge and an alert bot's delivered
    alert count are different numbers, and gating the sample by the
    former hides how often the strategy was actually right.
    """

    def __init__(self, candles, mode=None, m5=None, m1=None, target_mode=None,
                 record_all=False):
        self.candles = candles
        # record_all keeps candidates that scored below WATCH too. The
        # normal log is censored at WATCH_MIN_SCORE, which makes it
        # useless for asking "what would a LOWER threshold deliver?" --
        # exactly the question tools/tune_thresholds.py has to answer.
        self.record_all = record_all
        self.mode = mode or modes.STANDARD
        self.target_mode = target_mode or cfg.TARGET_MODE

        # Optional finer timeframes for the real M5 / M1 layers.
        self.m5 = m5 or []
        self.m1 = m1 or []
        self._m5_idx = index_by_time(self.m5)
        self._m1_idx = index_by_time(self.m1)
        self._m5_cursor = 0
        self._m1_cursor = 0

        # Precomputed HTF, sliced per scan by end index.
        self._h1_bars, self._h1_ends = precompute_htf(candles, 4)
        self._h4_bars, self._h4_ends = precompute_htf(candles, 16)

        # State that live persists to disk; here in-memory per-run.
        self.main_state = {}
        self.pending_a_plus = None          # (scored_dict, added_at_index)
        self.blocked_until_bar = -1         # simulated position still open
        self.signals = []

    # ── helpers ──────────────────────────────────────────────────────
    def _now(self, i):
        return pd.to_datetime(self.candles[i]["t"], utc=True).to_pydatetime()

    def _finer_upto(self, series, idx_map, cursor_attr, m15_ts, span_bars):
        """Slice a finer-timeframe series so it ends at the close of the
        M15 bar at m15_ts and never later.

        Exact-timestamp lookup first (the fast, unambiguous path); falls
        back to a monotonic cursor scan when the finer feed has gaps or a
        different alignment. Both are strictly backward-looking."""
        if not series:
            return []
        m15_close = pd.to_datetime(m15_ts, utc=True) + pd.Timedelta(minutes=15)
        cursor = getattr(self, cursor_attr)
        # Advance while the NEXT bar still closes at or before the M15 close.
        n = len(series)
        while cursor + 1 < n:
            nxt = pd.to_datetime(series[cursor + 1]["t"], utc=True)
            if nxt >= m15_close:
                break
            cursor += 1
        setattr(self, cursor_attr, cursor)
        lo = max(0, cursor + 1 - span_bars)
        return series[lo:cursor + 1]

    def _build_market(self, i):
        """Assemble the market bundle a live scan at bar i would have seen.

        Every timeframe is truncated to the SAME number of bars the live
        feed pulls (cfg.MTF_FETCH_BARS). Previously this passed the entire
        history from bar 0, which meant the SMC detectors computed swings,
        order blocks and CHOCH over thousands of bars in backtest but over
        160 bars live -- a live/backtest divergence in the data, defeating
        the point of sharing the code paths. It also made the whole run
        O(n^2).
        """
        bars = cfg.MTF_FETCH_BARS
        lo = max(0, i + 1 - bars["15min"])
        window = self.candles[lo: i + 1]
        ts = self.candles[i]["t"]
        h1 = self._h1_bars[: bisect_right(self._h1_ends, i)][-bars["1h"]:]
        h4 = self._h4_bars[: bisect_right(self._h4_ends, i)][-bars["4h"]:]
        return {
            "entry": window,
            "m15": window,
            "m5": self._finer_upto(self.m5, self._m5_idx, "_m5_cursor", ts,
                                   bars["5min"]),
            "m1": self._finer_upto(self.m1, self._m1_idx, "_m1_cursor", ts,
                                   bars["1min"]),
            "h1": h1,
            "h4": h4,
        }

    def _find(self, market):
        return strat.find_candidate(market["entry"], target_mode=self.target_mode)

    def _tick_pending(self, i):
        """Live analog: evaluate_pending_confirmations. Exactly one bar
        window: if pending was set at bar j, the confirmation bar is j+1."""
        if self.pending_a_plus is None:
            return
        scored, added_at = self.pending_a_plus
        if i <= added_at:
            return  # not yet — pending was just set this same bar
        if i == added_at + 1:
            last_closed = self.candles[i]
            direction = scored["direction"]
            if strat.confirmation_closed_in_direction(last_closed, direction):
                market = self._build_market(i)
                candidate = self._find(market)
                if candidate and candidate["direction"] == direction:
                    now = self._now(i)
                    rescored = strat.score_candidate(
                        "XAUUSD", "COMMODITY", candidate, market, now, None)
                    if rescored and rescored["score"] >= self.mode.aplus_min_score:
                        self._record("A+", rescored, i, market, suppressed=None)
        self.pending_a_plus = None

    # ── scan ─────────────────────────────────────────────────────────
    def scan(self, i):
        self._tick_pending(i)

        market = self._build_market(i)
        now = self._now(i)

        candidate = self._find(market)
        if candidate is None:
            return

        scored = strat.score_candidate(
            "XAUUSD", "COMMODITY", candidate, market, now, None)
        if not scored:
            return
        if scored["tier"] == "NONE":
            if self.record_all:
                # Simulated, but never tradeable and never occupies the
                # position gate -- it is distribution data, not an alert.
                self._record("NONE", scored, i, market, suppressed="below_threshold")
            return

        # From here on the setup IS an opportunity: it cleared the score
        # threshold. Whether it becomes a delivered alert is a separate
        # question, answered by the two gates below and recorded, not
        # used to drop the row.
        suppressed = None
        if i < self.blocked_until_bar:
            suppressed = "open_position"
        else:
            blocked, reason = ma.cooldown_blocks_alert(
                self.main_state, "XAUUSD", scored["direction"],
                scored["entry_price"], now)
            if blocked:
                suppressed = f"cooldown: {reason}"

        if suppressed:
            self._record(scored["tier"], scored, i, market, suppressed=suppressed)
            return

        if scored.get("aplus_eligible"):
            # A+ waits one bar for confirmation, mirrors PendingAPlusStore.
            if self.pending_a_plus is not None:
                self._record("A+", scored, i, market,
                             suppressed="pending_confirmation_busy")
                return
            self.pending_a_plus = (scored, i)
            ma.record_alert_for_cooldown(
                self.main_state, "XAUUSD", scored["direction"], scored["entry_price"], now)
            return

        self._record("WATCH", scored, i, market, suppressed=None)
        ma.record_alert_for_cooldown(
            self.main_state, "XAUUSD", scored["direction"], scored["entry_price"], now)

    # ── record + simulate ────────────────────────────────────────────
    def _record(self, tier, scored, i, market=None, suppressed=None):
        """Log one opportunity and simulate it under every cost regime.

        `suppressed` is None for a delivered alert, or a short reason
        string for an opportunity a live run would not have sent. Both
        are simulated identically -- suppression changes what the user
        would have received, not what the market did.
        """
        forward = self.candles[i + 1: i + 1 + _ENTRY_EXPIRY_BARS + _HOLD_EXPIRY_BARS]
        outcomes = {}
        for name, spread in COST_REGIMES:
            outcome, r, exit_offset = simulate_execution(scored, forward, spread_price=spread)
            outcomes[name] = {"outcome": outcome, "r": round(r, 3),
                              "exit_bar_offset": exit_offset}

        if suppressed is None:
            # Only a DELIVERED alert occupies the simulated position.
            realistic_exit = outcomes["realistic"]["exit_bar_offset"] or _ENTRY_EXPIRY_BARS
            self.blocked_until_bar = i + 1 + realistic_exit

        market = market or self._build_market(i)
        breakdown_map = {tag: pts for tag, pts in scored.get("breakdown", [])}

        row = {
            "bar_index": i,
            "t": self.candles[i]["t"],
            "tier": tier,
            "tradeable": suppressed is None,
            "suppressed_reason": suppressed,
            "pattern": scored["pattern"],
            "direction": scored["direction"],
            "score": scored["score"],
            "setup_quality": scored.get("setup_quality"),
            "breakdown": breakdown_map,
            "h4_bias": scored.get("htf_bias"),
            "zlsma_status": scored.get("zlsma_status"),
            "chop_regime": scored.get("chop_regime"),
            "target_mode": scored.get("target_mode"),
            "mtf_points": scored.get("mtf_points"),
            "mtf_available": scored.get("mtf_available"),
            # Execution
            "entry_price": scored["entry_price"],
            "stop_loss": scored["stop_loss"],
            "tp1": scored["tp1"], "tp2": scored["tp2"], "tp3": scored["tp3"],
            "risk": scored["risk"],
            "outcomes": outcomes,
        }
        # Look-ahead audit trail: last bar each timeframe was allowed to see.
        for key in ("m15", "h1", "h4", "m5", "m1"):
            series = market.get(key) or []
            row[f"{key}_last_t"] = series[-1]["t"] if series else None
            row[f"{key}_bars"] = len(series)
        self.signals.append(row)


def run_backtest(candles, mode=None, m5=None, m1=None, target_mode=None,
                 record_all=False):
    """Walk every bar, emit signals through the live pipeline."""
    run = BacktestRun(candles, mode=mode, m5=m5, m1=m1, target_mode=target_mode,
                      record_all=record_all)
    warmup = max(
        cfg.GT_ZLSMA_PERIOD * 2 + cfg.GT_ZLSMA_SLOPE_LOOKBACK + 5,
        16 * 30,   # need ≥30 H4 bars for the H4 regime read
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


def _bar_span_days(signals, candles=None):
    if not signals:
        return 0.0
    a = pd.to_datetime(signals[0]["t"], utc=True)
    b = pd.to_datetime(signals[-1]["t"], utc=True)
    return max((b - a).total_seconds() / 86400.0, 1e-9)


def _rate_block(name, subset, span_days):
    n = len(subset)
    per_week = n / span_days * 7 if span_days > 0 else 0.0
    print(f"  {name}: n={n}  ({per_week:.1f}/week)  {_fmt(_stats(subset))}")


def print_summary(signals, candles=None, target_mode=None):
    # Sub-threshold rows (only present with --record-all) are distribution
    # data for tools/tune_thresholds.py, never alerts. Counting them as
    # "opportunities" would silently inflate every rate in this report.
    below = [s for s in signals if s.get("suppressed_reason") == "below_threshold"]
    signals = [s for s in signals if s.get("suppressed_reason") != "below_threshold"]
    tradeable = [s for s in signals if s.get("tradeable", True)]
    suppressed = [s for s in signals if not s.get("tradeable", True)]

    print(f"\nTarget mode: {target_mode or cfg.TARGET_MODE}")
    print(f"Total opportunities recorded: {len(signals)}")
    if below:
        print(f"(+{len(below)} sub-threshold candidates logged for threshold "
              f"tuning; excluded from every rate below)")
    if not signals:
        return
    print(f"Span: {signals[0]['t']} → {signals[-1]['t']}")
    span_days = _bar_span_days(signals, candles)

    # ── the two rates the docstring promises ─────────────────────────
    print("\nRATES (realistic cost)")
    print("  These are different questions. The first measures the")
    print("  strategy; the second measures what a user would receive.")
    _rate_block("signal opportunity rate ", signals, span_days)
    _rate_block("tradeable alert rate    ", tradeable, span_days)
    if suppressed:
        print(f"\n  Suppressed ({len(suppressed)}) by reason:")
        for reason, n in Counter(
                (s.get("suppressed_reason") or "?").split(" (")[0]
                for s in suppressed).most_common():
            sub = [s for s in suppressed if (s.get("suppressed_reason") or "?").startswith(reason)]
            print(f"    {reason}: n={n}  {_fmt(_stats(sub))}")
        print("  If the suppressed set's expectancy materially differs from")
        print("  the tradeable set's, the gates are selecting on outcome and")
        print("  need re-examining -- not just thinning the stream.")

    # ── MTF coverage: never let an M15-only run be read as full-MTF ──
    cov = Counter(s.get("mtf_available") or "none" for s in signals)
    print("\nMTF coverage (timeframes with data at signal time):")
    for combo, n in cov.most_common():
        print(f"  {combo}: {n}")
    if not any("m5" in (k or "") for k in cov):
        print("  NOTE: no M5/M1 candles supplied -- those layers scored")
        print("  neutral (0 pts) throughout. Pass --m5/--m1 to exercise them.")

    tiers = Counter(s["tier"] for s in signals)
    print(f"\nBy tier: {dict(tiers)}")

    for cost, _ in COST_REGIMES:
        overall = _stats(signals, cost=cost)
        print(f"[{cost}] all opportunities: {_fmt(overall)}")

    def _splits(label, subset):
        print(f"\n{label} (realistic cost, all opportunities):")
        return subset

    _splits("By detector", signals)
    for pat in sorted({s["pattern"] for s in signals}):
        sub = [s for s in signals if s["pattern"] == pat]
        print(f"  {pat}: {_fmt(_stats(sub))}")

    _splits("By tier", signals)
    for tier in ["WATCH", "A+"]:
        sub = [s for s in signals if s["tier"] == tier]
        print(f"  {tier}: {_fmt(_stats(sub))}")

    _splits("By direction", signals)
    for direction in ["BUY", "SELL"]:
        sub = [s for s in signals if s["direction"] == direction]
        print(f"  {direction}: {_fmt(_stats(sub))}")

    _splits("By score band", signals)
    for lo, hi in [(0, 44), (45, 49), (50, 54), (55, 59), (60, 64),
                    (65, 69), (70, 74), (75, 79), (80, 84), (85, 100)]:
        sub = [s for s in signals if lo <= s["score"] <= hi]
        if sub:
            print(f"  {lo:>2}-{hi:>2}: {_fmt(_stats(sub))}")
    print("  (Monotonic expectancy across these bands is the evidence that")
    print("   the score means anything. Check it with tools/calibrate_scores.py.)")

    _splits("By session UTC", signals)
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
        sub = [s for s in signals if pred(_hour_of(s["t"]))]
        print(f"  {name}: {_fmt(_stats(sub))}")

    print("\nOutcome distribution (realistic, all opportunities):")
    counts = Counter(s["outcomes"]["realistic"]["outcome"] for s in signals)
    for outcome, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {outcome}: {n}")

    filled = [s for s in signals
              if not s["outcomes"]["realistic"]["outcome"].startswith("no_fill")]
    if filled:
        no_tp1 = ("stop_before_tp1", "time_expired_no_fill_progress",
                  "invalid_risk", "spread_erased_risk")
        tp1_hit = sum(1 for s in filled
                      if s["outcomes"]["realistic"]["outcome"] not in no_tp1)
        # breakeven_after_tp1 means TP1 then BE stop -- TP2 was NOT hit.
        tp2_hit = sum(1 for s in filled if s["outcomes"]["realistic"]["outcome"] in
                       ("runner_stopped", "tp3_runner_complete"))
        tp3_hit = sum(1 for s in filled
                      if s["outcomes"]["realistic"]["outcome"] == "tp3_runner_complete")
        print(f"\nTP hit rates (of {len(filled)} filled): "
              f"TP1={tp1_hit/len(filled)*100:.0f}% "
              f"TP2={tp2_hit/len(filled)*100:.0f}% "
              f"TP3={tp3_hit/len(filled)*100:.0f}%")
        print("  A high TP1 rate is not an edge on its own: exits are")
        print("  weighted 50/30/20, so expectancy (avg R above), not win")
        print("  rate, is the number that decides whether this is worth")
        print("  trading.")


def main():
    parser = argparse.ArgumentParser(description="Replay live pipeline over historical candles.")
    parser.add_argument("--candles", required=True,
                        help="M15 CSV path with columns t,o,h,l,c[,v] chronological")
    parser.add_argument("--m5", help="Optional M5 CSV; enables the real M5 confirmation layer")
    parser.add_argument("--m1", help="Optional M1 CSV; enables the real M1 timing layer")
    parser.add_argument("--target-mode", choices=["FIXED", "ATR"], default=None,
                        help="Override cfg.TARGET_MODE. Run both and compare "
                             "before concluding the fixed $25 ladder is right.")
    parser.add_argument("--record-all", action="store_true",
                        help="Also log candidates scoring below WATCH. Needed by\n"
                             "tools/tune_thresholds.py, which cannot reason about\n"
                             "lower thresholds from a log censored at the current one.")
    parser.add_argument("--json", help="Optional per-signal log JSON path")
    args = parser.parse_args()

    # Tests / CI / local runs shouldn't need real Telegram creds.
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "backtest-noop")
    os.environ.setdefault("TELEGRAM_CHAT_ID", "0")

    candles = load_candles(args.candles)
    print(f"Loaded {len(candles)} M15 candles from {args.candles}")
    m5 = load_candles(args.m5) if args.m5 else None
    m1 = load_candles(args.m1) if args.m1 else None
    if m5:
        print(f"Loaded {len(m5)} M5 candles from {args.m5}")
    if m1:
        print(f"Loaded {len(m1)} M1 candles from {args.m1}")

    target_mode = args.target_mode or cfg.TARGET_MODE
    signals = run_backtest(candles, m5=m5, m1=m1, target_mode=target_mode,
                           record_all=args.record_all)
    print_summary(signals, candles=candles, target_mode=target_mode)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(signals, f, indent=2, default=str)
        print(f"\nPer-signal log written to {args.json}")
        print("Next: python tools/calibrate_scores.py --signals "
              f"{args.json} --oos-split 0.7")


if __name__ == "__main__":
    main()
