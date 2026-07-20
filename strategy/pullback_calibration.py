"""
Bot Spec V4 Section 5 — offline pullback/retrace calibration.

Reuses the live bot's own pattern detectors (scoring_strategy.find_candidate)
against historical M15 candles to measure, per instrument: how deep a
pullback typically goes after a detected breakout (as a fraction of the real
leg), how often price tags a same-direction FVG inside that leg, how often a
given retrace-depth threshold actually gets filled, and how long that fill
typically takes.

Outputs a percentile table and a *recommended* retrace_pct per instrument.
This module only recommends -- it never writes strategy_config.py itself.
A human reviews the printed report and updates INSTRUMENT_PROFILES by hand.

Manual-trigger only (see .github/workflows/calibrate.yml) -- not part of the
live scan cron/workflow, and never called from main_alerts.run().
"""
import argparse
import statistics

import scoring_indicators as ind
import scoring_strategy as strat
import strategy_config as cfg
from strategy.capital_feed import CapitalFeed

RETRACE_DEPTHS_TO_TEST = [0.30, 0.40, 0.50, 0.60, 0.70]
LOOKAHEAD_BARS = 20          # M15 bars after the breakout to watch for a fill
MIN_HISTORY_BARS = 80        # matches build_market's live entry-candle window
MIN_FILL_RATE_FOR_RECOMMENDATION = 0.55


def find_historical_breakouts(candles, min_history=MIN_HISTORY_BARS):
    """Replay the live detectors bar-by-bar over historical M15 candles,
    reusing find_candidate() exactly as the live scan does -- not a forked
    or reimplemented copy. Returns [{"index": i, "candidate": {...}}, ...]
    for every bar where a pattern fired."""
    breakouts = []
    for i in range(min_history, len(candles)):
        window = candles[max(0, i - min_history):i + 1]
        candidate = strat.find_candidate(window)
        if candidate:
            breakouts.append({"index": i, "candidate": candidate})
    return breakouts


def measure_breakout(candles, breakout, lookahead=LOOKAHEAD_BARS):
    """For one detected breakout, measure the deepest pullback actually
    reached (as a fraction of the leg), whether it tagged a same-direction
    FVG inside the leg, and -- for each depth in RETRACE_DEPTHS_TO_TEST --
    whether and how many bars later a resting limit at that depth would have
    filled. Returns None if the leg is degenerate or there's no future data
    to look ahead into (mirrors compute_entry_exit's own degenerate-leg
    handling, so the sample only reflects setups the live bot could actually
    have traded)."""
    idx = breakout["index"]
    candidate = breakout["candidate"]
    direction = candidate["direction"]
    leg_extreme = candidate.get("leg_extreme", candidate["sweep_price"])
    close = candles[idx]["c"]

    leg_low, leg_high = (leg_extreme, close) if direction == "BUY" else (close, leg_extreme)
    leg_size = leg_high - leg_low
    if leg_size <= 0:
        return None

    future = candles[idx + 1: idx + 1 + lookahead]
    if not future:
        return None

    if direction == "BUY":
        deepest = min(c["l"] for c in future)
        depth_reached = (close - deepest) / leg_size
    else:
        deepest = max(c["h"] for c in future)
        depth_reached = (deepest - close) / leg_size
    depth_reached = max(0.0, min(1.0, depth_reached))

    wanted_dir = "BULLISH" if direction == "BUY" else "BEARISH"
    fvg_zones = [z for z in ind.detect_fvg_zones(candles[max(0, idx - 10):idx + 1])
                 if z["direction"] == wanted_dir]
    tagged_fvg = any(leg_low <= z["bottom"] and z["top"] <= leg_high for z in fvg_zones)

    fill_results = {}
    for pct in RETRACE_DEPTHS_TO_TEST:
        level = close - leg_size * pct if direction == "BUY" else close + leg_size * pct
        filled_at = None
        for j, c in enumerate(future):
            touched = c["l"] <= level if direction == "BUY" else c["h"] >= level
            if touched:
                filled_at = j + 1  # bars until fill
                break
        fill_results[pct] = filled_at

    return {"depth_reached": depth_reached, "tagged_fvg": tagged_fvg, "fill_results": fill_results}


def _percentile(sorted_values, pct):
    if not sorted_values:
        return None
    idx = max(0, min(len(sorted_values) - 1, int(round(pct / 100 * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def calibrate_instrument(feed, instrument, n=1500):
    """Pull n historical M15 candles for `instrument`, replay the live
    detectors over them, and return a calibration summary -- or None if too
    little history/too few detected breakouts to say anything useful."""
    candles = feed.get_candles(instrument, "15min", n=n)
    breakouts = find_historical_breakouts(candles)
    measurements = [m for m in (measure_breakout(candles, b) for b in breakouts) if m is not None]
    if not measurements:
        return None

    depths = sorted(m["depth_reached"] for m in measurements)
    depth_percentiles = {p: _percentile(depths, p) for p in (25, 50, 75, 90)}
    fvg_tag_rate = sum(1 for m in measurements if m["tagged_fvg"]) / len(measurements)

    fill_stats = {}
    for pct in RETRACE_DEPTHS_TO_TEST:
        fills = [m["fill_results"][pct] for m in measurements if m["fill_results"][pct] is not None]
        fill_stats[pct] = {
            "fill_rate": len(fills) / len(measurements),
            "avg_bars_to_fill": statistics.mean(fills) if fills else None,
        }

    # Prefer the shallowest tested depth that still fills often enough --
    # a shallower retrace gives a better entry price when it does fill, so
    # there's no reason to recommend deeper than necessary to clear the
    # fill-rate floor.
    recommended = next(
        (pct for pct in RETRACE_DEPTHS_TO_TEST
         if fill_stats[pct]["fill_rate"] >= MIN_FILL_RATE_FOR_RECOMMENDATION),
        RETRACE_DEPTHS_TO_TEST[-1],
    )

    return {
        "instrument": instrument, "sample_size": len(measurements),
        "depth_percentiles": depth_percentiles, "fvg_tag_rate": fvg_tag_rate,
        "fill_stats": fill_stats, "recommended_retrace_pct": recommended,
    }


def format_report(results):
    lines = [
        "Pullback calibration report — RECOMMENDATIONS ONLY.",
        "Nothing here is auto-applied; a human confirms before editing "
        "strategy_config.INSTRUMENT_PROFILES by hand.\n",
    ]
    for r in results:
        if r is None:
            continue
        p = r["depth_percentiles"]
        lines.append(f"=== {r['instrument']} (n={r['sample_size']} historical breakouts) ===")
        lines.append(
            f"  Pullback depth reached, as a fraction of the leg — "
            f"p25={p[25]:.2f} p50={p[50]:.2f} p75={p[75]:.2f} p90={p[90]:.2f}"
        )
        lines.append(f"  Same-direction FVG tagged inside the leg: {r['fvg_tag_rate']:.0%} of breakouts")
        for pct in RETRACE_DEPTHS_TO_TEST:
            fs = r["fill_stats"][pct]
            avg = f"{fs['avg_bars_to_fill']:.1f} bars" if fs["avg_bars_to_fill"] is not None else "n/a"
            lines.append(f"  retrace {pct:.0%}: fill rate {fs['fill_rate']:.0%}, avg time to fill {avg}")
        lines.append(f"  Recommended retrace_pct: {r['recommended_retrace_pct']:.2f}\n")
    return "\n".join(lines)


def run(instruments=None, n=1500):
    feed = CapitalFeed()
    feed.open_session()
    feed.resolve_epics()
    instruments = instruments or list(cfg.INSTRUMENTS)
    results = [calibrate_instrument(feed, instrument, n=n) for instrument in instruments]
    report = format_report(results)
    print(report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instruments", nargs="*", default=None,
                         help="Subset of instruments to calibrate (default: all in strategy_config.INSTRUMENTS)")
    parser.add_argument("--bars", type=int, default=1500, help="Historical M15 bars to pull per instrument")
    args = parser.parse_args()
    run(instruments=args.instruments, n=args.bars)
