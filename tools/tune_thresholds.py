"""
Derive WATCH / A+ thresholds from a target alert cadence.

THE PROBLEM THIS SOLVES
───────────────────────
Thresholds have twice been set by hand and then inherited through changes
that altered what the score means. After the scoring rewrite, A+ at 70
fired on 4 of 573 signals (0.7%) against a practical score ceiling of 76
— silent. Lower it by guesswork and the failure mode flips to noisy.

Neither "loose" nor "strict" is a property of the number; both are
properties of the *delivered alert rate*, which depends on the score
distribution AND on the cooldown and one-position gates. So tune the rate
directly and let the thresholds fall out.

WHAT IS AND IS NOT TUNABLE ON SYNTHETIC DATA
────────────────────────────────────────────
Cadence is structural: it depends on how often setups appear, how the
score spreads them out, and how fast trades resolve and release the
position gate. Those survive the move to real candles reasonably well.

Expectancy does NOT. A synthetic price path has no real edge, so any win
rate or R figure from it is noise. This tool therefore reports cadence
only, and deliberately refuses to rank thresholds by profitability.
Once real history is available, use tools/calibrate_scores.py to check
that the score actually orders outcomes, and re-run this to set the level.

Usage:
    python backtest.py --candles gold.csv --record-all --json all.json
    python tools/tune_thresholds.py --signals all.json \\
        --aplus-per-week 5 --watch-per-week 20
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd


def span_weeks(signals):
    ts = sorted(pd.to_datetime(s["t"], utc=True) for s in signals)
    days = (ts[-1] - ts[0]).total_seconds() / 86400.0
    return max(days / 7.0, 1e-9)


def delivered_at(signals, watch_min, aplus_min, weeks):
    """Replay the gates at a candidate threshold pair.

    A signal is only *delivered* if it clears the threshold AND was not
    suppressed. Suppression is recorded per-signal by the backtester;
    'below_threshold' rows were never real alerts, so they are treated as
    eligible here and re-tested against the candidate threshold.
    """
    elig = [s for s in signals if s["score"] >= watch_min]
    # A row suppressed by cooldown/position under the ORIGINAL thresholds
    # would also have been suppressed under a lower one (the blocking
    # trade still exists), so keep those suppressed. Rows suppressed only
    # for being below threshold become deliverable if they now clear it.
    deliverable = [s for s in elig
                   if s.get("tradeable") or s.get("suppressed_reason") == "below_threshold"]
    aplus = [s for s in deliverable if s["score"] >= aplus_min]
    watch = [s for s in deliverable if s["score"] < aplus_min]
    return len(aplus) / weeks, len(watch) / weeks, len(elig) / weeks


def pick(signals, target_aplus, target_watch, weeks):
    """Smallest thresholds whose delivered rates land nearest the targets."""
    scores = sorted({s["score"] for s in signals})
    best = None
    for a in scores:
        for w in scores:
            if w >= a:
                continue
            ar, wr, _ = delivered_at(signals, w, a, weeks)
            # Distance in relative terms so neither target dominates.
            cost = abs(ar - target_aplus) / max(target_aplus, 1e-9) \
                 + abs(wr - target_watch) / max(target_watch, 1e-9)
            if best is None or cost < best[0]:
                best = (cost, w, a, ar, wr)
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--signals", required=True,
                    help="JSON from backtest.py --record-all --json")
    ap.add_argument("--aplus-per-week", type=float, default=5.0)
    ap.add_argument("--watch-per-week", type=float, default=20.0)
    args = ap.parse_args()

    signals = json.load(open(args.signals))
    if not signals:
        print("no signals in log")
        return
    weeks = span_weeks(signals)
    scores = [s["score"] for s in signals]
    below = sum(1 for s in signals if s.get("suppressed_reason") == "below_threshold")
    if below == 0:
        print("WARNING: this log has no sub-threshold rows, so it is censored at\n"
              "         the current WATCH threshold and cannot show what a lower\n"
              "         one would deliver. Re-run backtest.py with --record-all.\n")

    print(f"{len(signals)} scored candidates over {weeks:.1f} weeks")
    print(f"score distribution: min={min(scores)} median={sorted(scores)[len(scores)//2]} "
          f"p90={sorted(scores)[int(len(scores)*0.9)]} max={max(scores)}")

    print("\nDelivered rate by threshold (after cooldown + position gates):")
    print(f"  {'A+ min':>7} {'A+/wk':>7} {'WATCH min':>10} {'WATCH/wk':>9}")
    for a in (55, 60, 62, 65, 68, 70, 75):
        for w in (45, 50):
            if w >= a:
                continue
            ar, wr, _ = delivered_at(signals, w, a, weeks)
            print(f"  {a:>7} {ar:>7.1f} {w:>10} {wr:>9.1f}")

    best = pick(signals, args.aplus_per_week, args.watch_per_week, weeks)
    if best:
        _cost, w, a, ar, wr = best
        print(f"\nTargets: A+ {args.aplus_per_week}/wk, WATCH {args.watch_per_week}/wk")
        print(f"  → WATCH_MIN_SCORE = {w}")
        print(f"  → APLUS_MIN_SCORE = {a}")
        print(f"    delivers A+ {ar:.1f}/wk, WATCH {wr:.1f}/wk")
    print("\nCadence only. This says nothing about whether the score predicts\n"
          "outcomes — that is tools/calibrate_scores.py, on real history.")


if __name__ == "__main__":
    main()
