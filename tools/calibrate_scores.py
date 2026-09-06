"""
Score calibration: what expectancy does a score of X actually represent?

The scoring engine normalises Golden Trio and SMC onto one 0..100 scale.
That makes them *comparable*; it does not make them *equivalent*. A GT
setup scoring 75 and an SMC setup scoring 75 are only interchangeable if
the market paid them the same, and the only way to know that is to
measure it.

This tool reads a backtest signal log (backtest.py --json) and reports:

  1. Realized expectancy per score bucket, with the count in each. If the
     score means anything, avg R should rise monotonically across
     buckets. If it doesn't, the score is not a confidence measure and
     the thresholds are arbitrary lines on a noisy axis.

  2. The same table split by detector, which is the direct test of the
     normalisation: if GT 70-79 and SMC 70-79 have materially different
     expectancy, the two detectors are NOT on a common axis regardless of
     sharing a budget, and PATTERN_QUALITY_BASE_MAX / GT_QUALITY_WEIGHT_*
     need reweighting from these numbers.

  3. An in-sample / out-of-sample split. Buckets fitted on the whole
     sample will always look tidy; the out-of-sample half is the only
     half that is evidence. A relationship that holds in-sample and
     vanishes out-of-sample is overfitting, and is reported as such.

  4. A per-component marginal read: for each score component, the
     expectancy of signals that received it vs. those that didn't. A
     component whose presence does not improve expectancy is costing
     score budget for nothing.

Sample-size discipline: buckets below MIN_BUCKET_N are printed but
flagged, and never described as evidence. With gold M15 and a realistic
signal rate, a few hundred signals is a preliminary read, not a
validated result.

Usage:
    python backtest.py --candles XAUUSD_M15.csv --json signals.json
    python tools/calibrate_scores.py --signals signals.json --oos-split 0.7
    python tools/calibrate_scores.py --signals signals.json --tradeable-only
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIN_BUCKET_N = 30          # below this, a bucket is noise, not a reading
DEFAULT_BUCKETS = [(0, 44), (45, 54), (55, 64), (65, 74), (75, 84), (85, 100)]
NO_FILL_PREFIX = "no_fill"


def load_signals(path):
    with open(path) as f:
        return json.load(f)


def _filled(signals, cost):
    return [s for s in signals
            if not str(s["outcomes"][cost]["outcome"]).startswith(NO_FILL_PREFIX)]


def stats(signals, cost="realistic"):
    """n / win rate / expectancy / profit factor over FILLED signals."""
    filled = _filled(signals, cost)
    if not filled:
        return {"n": 0, "n_raw": len(signals), "wr": 0.0, "avg_r": 0.0,
                "total_r": 0.0, "pf": 0.0, "se": 0.0}
    rs = [s["outcomes"][cost]["r"] for s in filled]
    wins = [r for r in rs if r > 0]
    gross_win = sum(wins)
    gross_loss = -sum(r for r in rs if r < 0)
    mean = sum(rs) / len(rs)
    if len(rs) > 1:
        var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)
        se = (var / len(rs)) ** 0.5
    else:
        se = 0.0
    return {
        "n": len(filled),
        "n_raw": len(signals),
        "wr": len(wins) / len(filled),
        "avg_r": mean,
        "total_r": sum(rs),
        "pf": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "se": se,
    }


def fmt(st):
    if not st or st["n"] == 0:
        return f"n=0 (of {st['n_raw'] if st else 0} raw)"
    pf = "inf" if st["pf"] == float("inf") else f"{st['pf']:.2f}"
    flag = "" if st["n"] >= MIN_BUCKET_N else "  ⚠ under-powered"
    # ±1.96 SE on the mean R: the honest error bar on an expectancy claim.
    return (f"n={st['n']:<4} wr={st['wr']*100:4.0f}%  "
            f"avg={st['avg_r']:+.3f}R ±{1.96*st['se']:.3f}  "
            f"total={st['total_r']:+7.1f}R  pf={pf}{flag}")


def bucket_of(score, buckets):
    for lo, hi in buckets:
        if lo <= score <= hi:
            return (lo, hi)
    return None


def report_buckets(title, signals, buckets, cost):
    print(f"\n{title}")
    print("-" * len(title))
    if not signals:
        print("  (no signals)")
        return []
    rows = []
    for lo, hi in buckets:
        sub = [s for s in signals if lo <= s["score"] <= hi]
        if not sub:
            continue
        st = stats(sub, cost)
        rows.append(((lo, hi), st))
        print(f"  {lo:>3}-{hi:<3}  {fmt(st)}")
    return rows


def monotonicity(rows):
    """Report whether expectancy actually increases with score.

    Uses only buckets with enough signals to mean anything -- an
    'increasing' trend carried by a 4-signal bucket is not a trend.
    """
    usable = [(b, st) for b, st in rows if st["n"] >= MIN_BUCKET_N]
    if len(usable) < 3:
        return ("insufficient",
                f"only {len(usable)} bucket(s) with n>={MIN_BUCKET_N}; "
                "cannot assess whether the score is informative")
    avgs = [st["avg_r"] for _, st in usable]
    ups = sum(1 for a, b in zip(avgs, avgs[1:]) if b > a)
    pairs = len(avgs) - 1
    if ups == pairs:
        return ("monotonic", f"expectancy rises across all {pairs} adjacent bucket pairs")
    if ups >= pairs * 0.7:
        return ("mostly", f"expectancy rises in {ups}/{pairs} adjacent pairs")
    return ("not monotonic",
            f"expectancy rises in only {ups}/{pairs} adjacent pairs -- the "
            "score is not ordering outcomes")


def report_components(signals, cost):
    """Marginal value of each score component.

    For every component tag seen in the breakdown, compare signals that
    received it against those that didn't. A component that doesn't move
    expectancy is spending score budget for nothing.
    """
    tags = Counter()
    for s in signals:
        for tag in (s.get("breakdown") or {}):
            tags[tag] += 1
    if not tags:
        print("\n  (no breakdown data in this log)")
        return
    print("\nPer-component marginal expectancy (realistic cost)")
    print("-" * 50)
    print(f"  {'component':<24}{'with':<34}{'without'}")
    for tag, count in tags.most_common():
        if count < MIN_BUCKET_N:
            continue
        with_ = [s for s in signals if tag in (s.get("breakdown") or {})]
        without = [s for s in signals if tag not in (s.get("breakdown") or {})]
        a, b = stats(with_, cost), stats(without, cost)
        if a["n"] == 0 or b["n"] == 0:
            continue
        delta = a["avg_r"] - b["avg_r"]
        print(f"  {tag:<24}n={a['n']:<4} {a['avg_r']:+.3f}R      "
              f"n={b['n']:<4} {b['avg_r']:+.3f}R   Δ={delta:+.3f}R")
    print("  Δ is the component's realized edge, not its assigned points.")
    print("  A component with Δ ≈ 0 (or negative) is mis-weighted.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--signals", required=True, help="JSON from backtest.py --json")
    ap.add_argument("--cost", default="realistic",
                    choices=["ideal", "realistic", "conservative"])
    ap.add_argument("--oos-split", type=float, default=0.7,
                    help="Fraction of the series used as in-sample; the "
                         "remainder is held out. 0 disables the split.")
    ap.add_argument("--tradeable-only", action="store_true",
                    help="Restrict to alerts that survived cooldown/position "
                         "gates. Default is every opportunity, which is the "
                         "larger and less selection-biased sample.")
    args = ap.parse_args()

    signals = load_signals(args.signals)
    if args.tradeable_only:
        signals = [s for s in signals if s.get("tradeable", True)]
    signals.sort(key=lambda s: str(s["t"]))

    print(f"Loaded {len(signals)} signals from {args.signals}  (cost: {args.cost})")
    if signals:
        print(f"Span: {signals[0]['t']} → {signals[-1]['t']}")
    print(f"Sample scope: {'tradeable alerts only' if args.tradeable_only else 'all opportunities'}")

    if len(signals) < MIN_BUCKET_N * 3:
        print(f"\n⚠ {len(signals)} signals is too small a sample for calibration.")
        print("  Anything below is descriptive of this particular window and")
        print("  should not be quoted as a win rate or an expectancy.")

    overall = stats(signals, args.cost)
    print(f"\nOverall: {fmt(overall)}")

    rows = report_buckets("Expectancy by score bucket (full sample)",
                          signals, DEFAULT_BUCKETS, args.cost)
    verdict, why = monotonicity(rows)
    print(f"\n  Score informativeness: {verdict.upper()} — {why}")

    print("\n\nBy detector — the direct test of the shared quality axis")
    print("=" * 56)
    by_det = defaultdict(list)
    for s in signals:
        by_det[s["pattern"]].append(s)
    for pattern, sub in sorted(by_det.items()):
        report_buckets(f"{pattern}  (n={len(sub)})", sub, DEFAULT_BUCKETS, args.cost)
    print("\n  Compare the same bucket across detectors. Materially different")
    print("  expectancy in the same bucket means the normalisation is")
    print("  cosmetic and the weights need refitting to these numbers.")

    if args.oos_split and 0 < args.oos_split < 1 and signals:
        cut = int(len(signals) * args.oos_split)
        in_s, out_s = signals[:cut], signals[cut:]
        print("\n\nWalk-forward split")
        print("=" * 56)
        print(f"In-sample:     {len(in_s)} signals  "
              f"({in_s[0]['t'] if in_s else '-'} → {in_s[-1]['t'] if in_s else '-'})")
        print(f"Out-of-sample: {len(out_s)} signals  "
              f"({out_s[0]['t'] if out_s else '-'} → {out_s[-1]['t'] if out_s else '-'})")
        in_rows = report_buckets("IN-SAMPLE", in_s, DEFAULT_BUCKETS, args.cost)
        out_rows = report_buckets("OUT-OF-SAMPLE", out_s, DEFAULT_BUCKETS, args.cost)
        v_in, _ = monotonicity(in_rows)
        v_out, why_out = monotonicity(out_rows)
        print(f"\n  In-sample:     {v_in.upper()}")
        print(f"  Out-of-sample: {v_out.upper()} — {why_out}")
        if v_in in ("monotonic", "mostly") and v_out == "not monotonic":
            print("\n  ⚠ The score orders outcomes in-sample but not out-of-sample.")
            print("    That is the signature of overfitting. Do not quote the")
            print("    in-sample numbers as the strategy's performance.")
        elif v_out == "insufficient":
            print("\n  The out-of-sample half is too small to conclude anything.")
            print("    Collect more history before making a performance claim.")

    report_components(signals, args.cost)

    print("\n" + "=" * 56)
    print("Reminder: none of this is out-of-sample unless the parameters")
    print("were fixed BEFORE the data in the held-out half was seen. A")
    print("split computed after tuning on the whole file is not a")
    print("validation, it is a presentation.")


if __name__ == "__main__":
    main()
