#!/usr/bin/env python3
"""Head-to-head between two entry premises over identical candles.

WHY THIS EXISTS
───────────────
tools/premise_check.py measured the reversion premise on 25,000 real M15
bars and did not find it: after a 10-bar HIGH the next 10-bar move was
+0.883 (t=+2.4), where a bounce premise needs it NEGATIVE. That is a
reason to TEST a continuation entry, not evidence that one is
profitable. This script is what turns that into a measurement.

It deliberately reports the DIFFERENCE with an error bar, because two
expectancies that both straddle zero can look different while being
statistically indistinguishable -- which is exactly the trap earlier
rounds of this project fell into. Welch's t-test on the two R
distributions, plus the paired result on the bars where both modes
traded, which removes most of the shared market noise.

Usage:
    python tools/compare_entry_modes.py --a run.json --b run.MOMENTUM.json
"""
import argparse
import json
import math
import statistics as st

NO_FILL_PREFIX = "no_fill"
COSTS = ("ideal", "realistic", "conservative")


def _load(path):
    with open(path) as f:
        rows = json.load(f)
    # Sub-threshold rows are distribution data, never alerts. Counting
    # them here would compare two different populations.
    return [r for r in rows if r.get("suppressed_reason") != "below_threshold"]


def _fills(rows, cost):
    return [r for r in rows
            if not str(r["outcomes"][cost]["outcome"]).startswith(NO_FILL_PREFIX)]


def _stats(rows, cost):
    f = _fills(rows, cost)
    if not f:
        return None
    rs = [float(r["outcomes"][cost]["r"]) for r in f]
    wins = [x for x in rs if x > 0]
    gross_w = sum(wins)
    gross_l = -sum(x for x in rs if x < 0)
    sd = st.pstdev(rs) if len(rs) > 1 else 0.0
    return {
        "n": len(rs),
        "wr": len(wins) / len(rs),
        "mean": st.mean(rs),
        "sd": sd,
        "se": sd / math.sqrt(len(rs)) if rs else 0.0,
        "pf": (gross_w / gross_l) if gross_l else float("inf"),
    }


def _welch(a, b):
    """Return (diff, t, approx_p) for mean(b) - mean(a)."""
    if not a or not b or a["n"] < 2 or b["n"] < 2:
        return None, None, None
    diff = b["mean"] - a["mean"]
    se = math.sqrt(a["se"] ** 2 + b["se"] ** 2)
    if se == 0:
        return diff, None, None
    t = diff / se
    # Normal approximation is fine at these sample sizes.
    p = math.erfc(abs(t) / math.sqrt(2))
    return diff, t, p


def _paired(rows_a, rows_b, cost):
    """Compare only bars where BOTH modes produced a filled trade.

    Both arms see the same market, so the unpaired difference carries a
    lot of common noise. Pairing on bar_index removes it."""
    idx_a = {r.get("bar_index"): r for r in _fills(rows_a, cost)}
    idx_b = {r.get("bar_index"): r for r in _fills(rows_b, cost)}
    # Parenthesised deliberately: `-` binds tighter than `&`, so the
    # unbracketed form reads as a different expression than intended.
    shared = sorted((set(idx_a) & set(idx_b)) - {None})
    if len(shared) < 2:
        return None
    d = [float(idx_b[i]["outcomes"][cost]["r"]) - float(idx_a[i]["outcomes"][cost]["r"])
         for i in shared]
    sd = st.pstdev(d)
    se = sd / math.sqrt(len(d)) if d else 0.0
    return {
        "n": len(d),
        "mean": st.mean(d),
        "se": se,
        "t": (st.mean(d) / se) if se else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="baseline signal JSON")
    ap.add_argument("--b", required=True, help="challenger signal JSON")
    ap.add_argument("--label-a", default=None)
    ap.add_argument("--label-b", default=None)
    args = ap.parse_args()

    rows_a, rows_b = _load(args.a), _load(args.b)
    la = args.label_a or (rows_a[0].get("entry_mode") if rows_a else "A") or "A"
    lb = args.label_b or (rows_b[0].get("entry_mode") if rows_b else "B") or "B"

    print(f"\nENTRY-MODE HEAD-TO-HEAD   {la}  vs  {lb}")
    print(f"  {la}: {len(rows_a)} opportunities    {lb}: {len(rows_b)} opportunities")
    print("  (identical candles required -- otherwise this comparison is void)\n")

    print(f"  {'cost':<14}{'mode':<11}{'n':>6}{'win%':>7}{'avgR':>9}"
          f"{'±1.96SE':>10}{'PF':>7}")
    verdicts = []
    for cost in COSTS:
        sa, sb = _stats(rows_a, cost), _stats(rows_b, cost)
        for lbl, s in ((la, sa), (lb, sb)):
            if not s:
                print(f"  {cost:<14}{lbl:<11}{'--':>6}  (no fills)")
                continue
            print(f"  {cost:<14}{lbl:<11}{s['n']:>6}{s['wr']*100:>6.0f}%"
                  f"{s['mean']:>+9.3f}{1.96*s['se']:>10.3f}{s['pf']:>7.2f}")
        diff, t, p = _welch(sa, sb)
        if diff is not None and t is not None:
            sig = "SIGNIFICANT" if abs(t) > 2 else "not significant"
            print(f"  {'':<14}{'diff':<11}{'':>6}{'':>7}{diff:>+9.3f}"
                  f"{'':>10}   t={t:+.1f}  p={p:.3f}  {sig}")
        pr = _paired(rows_a, rows_b, cost)
        if pr and pr["t"] is not None:
            sig = "SIGNIFICANT" if abs(pr["t"]) > 2 else "not significant"
            print(f"  {'':<14}{'paired':<11}{pr['n']:>6}{'':>7}{pr['mean']:>+9.3f}"
                  f"{1.96*pr['se']:>10.3f}   t={pr['t']:+.1f}  {sig}")
        # The verdict is driven by the PAIRED test where it is available,
        # because that is the one this report calls the stronger of the
        # two. Reading the verdict off the unpaired diff while telling
        # the reader to prefer the paired row would be incoherent.
        if pr and pr["t"] is not None:
            verdicts.append((cost, pr["mean"], pr["t"], "paired"))
        elif diff is not None and t is not None:
            verdicts.append((cost, diff, t, "unpaired"))
        print()

    print("READING THIS")
    print("  The 'diff' row is what the head-to-head is for. Two means that")
    print("  both straddle zero can differ by a lot and still be one sample")
    print("  of noise; |t| > 2 is the minimum before the difference is worth")
    print("  a second look, and even then it is ONE instrument over ONE")
    print("  window with several variants tried.")
    print("  The 'paired' row compares only bars where both modes traded, so")
    print("  it removes shared market noise -- treat it as the stronger of")
    print("  the two tests when its n is reasonable.")
    if verdicts:
        best = max(verdicts, key=lambda v: abs(v[2]))
        cost, mean, t, kind = best
        if abs(t) <= 2:
            print("\n  VERDICT: no significant difference at any cost level.")
            print("  Neither premise is demonstrated better than the other here.")
        else:
            who = lb if mean > 0 else la
            print(f"\n  VERDICT: {who} leads at '{cost}' on the {kind} test "
                  f"(t={t:+.1f}).")
            print("  Confirm on a window this choice has NOT seen before")
            print("  treating it as real -- every earlier finding in this")
            print("  project that skipped that step later reversed.")

        # Where the two tests disagree, say so loudly. It means the
        # challenger's advantage comes from the bars it alone trades
        # rather than from being better on common ground -- a selection
        # effect, not a better premise.
        unpaired = {}
        for c in COSTS:
            sa, sb = _stats(rows_a, c), _stats(rows_b, c)
            d, tt, _ = _welch(sa, sb)
            if d is not None:
                unpaired[c] = d
        paired = {c: m for c, m, _t, k in verdicts if k == "paired"}
        clashes = [c for c in paired
                   if c in unpaired and paired[c] * unpaired[c] < 0]
        if clashes:
            print("\n  ⚠ PAIRED AND UNPAIRED DISAGREE at: "
                  + ", ".join(clashes))
            print("  The two modes rank one way over their full populations")
            print("  and the other way on the bars they both traded. That")
            print("  means the difference is WHICH bars each mode selects,")
            print("  not how well either handles the same bar. Do not quote")
            print("  the unpaired number as if one premise beat the other.")


if __name__ == "__main__":
    main()
