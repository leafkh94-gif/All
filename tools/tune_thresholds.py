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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd


def span_weeks(signals):
    ts = sorted(pd.to_datetime(s["t"], utc=True) for s in signals)
    days = (ts[-1] - ts[0]).total_seconds() / 86400.0
    return max(days / 7.0, 1e-9)


def run_watch_floor(signals):
    """The WATCH threshold the logged run itself used.

    Rows at or above it competed for the cooldown and one-position gates
    for real. Rows below it were never alerts, so they never occupied the
    gate and never blocked anything.
    """
    real = [s["score"] for s in signals
            if s.get("suppressed_reason") != "below_threshold"]
    return min(real) if real else None


def delivered_at(signals, watch_min, aplus_min, weeks):
    """Replay the gates at a candidate threshold pair.

    Returns (aplus_per_week, watch_per_week, reliable).

    `reliable` is False when watch_min drops below the threshold the run
    actually used. Below that line the answer is an UPPER BOUND, not a
    prediction: those extra candidates never competed for the position
    gate, so counting them as delivered assumes a gate that was never
    tested. Some of them would have blocked each other, and some would
    have blocked alerts that did fire. Only a re-run at the candidate
    threshold settles it.

    At or above the run's own floor the numbers are exact, because
    splitting genuinely-delivered alerts by an A+ line is only
    re-labelling them.
    """
    floor = run_watch_floor(signals)
    reliable = floor is None or watch_min >= floor

    elig = [s for s in signals if s["score"] >= watch_min]
    deliverable = [s for s in elig
                   if s.get("tradeable") or s.get("suppressed_reason") == "below_threshold"]
    aplus = [s for s in deliverable if s["score"] >= aplus_min]
    watch = [s for s in deliverable if s["score"] < aplus_min]
    return len(aplus) / weeks, len(watch) / weeks, reliable


def pick(signals, target_aplus, target_watch, weeks):
    """Smallest thresholds whose delivered rates land nearest the targets."""
    scores = sorted({s["score"] for s in signals})
    best = None
    for a in scores:
        for w in scores:
            if w >= a:
                continue
            ar, wr, reliable = delivered_at(signals, w, a, weeks)
            if not reliable:
                # Never recommend a threshold whose rate we can only
                # bound from above -- that is how a tuner talks you into
                # a noisy configuration.
                continue
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

    floor = run_watch_floor(signals)
    print(f"\nThe logged run used WATCH >= {floor}. At or above that line the")
    print("rates below are exact. Below it they are upper bounds: those")
    print("candidates never competed for the one-position gate.")
    print("\nDelivered rate by threshold (after cooldown + position gates):")
    print(f"  {'A+ min':>7} {'A+/wk':>7} {'WATCH min':>10} {'WATCH/wk':>9}  ")
    for a in (48, 50, 52, 55, 58, 60, 65, 70):
        for w in (floor,):
            if w is None or w >= a:
                continue
            ar, wr, rel = delivered_at(signals, w, a, weeks)
            flag = "" if rel else "  (upper bound)"
            print(f"  {a:>7} {ar:>7.1f} {w:>10} {wr:>9.1f}{flag}")

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
