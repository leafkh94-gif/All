"""
Generate a volatility-realistic synthetic XAUUSD M15 series.

WHY THIS EXISTS
───────────────
Thresholds and target distances were previously tuned against a plain
Gaussian random walk whose M15 ATR came out around $1.74. Real gold's
measured median M15 ATR is $8.76, so every distance-based constant
calibrated on that walk was wrong by ~5x: a $25 stop looked like 14x ATR
when it is really ~2.85x, TP3 at $100 looked like 57x, and ~70% of
trades appeared to resolve to neither target nor stop inside four days.
Those were artefacts of the generator, not properties of the strategy.

This file's first attempt at a fix still guessed -- $3.5 -- and was
itself 2.5x too quiet, which is what produced the (now retracted) claim
that the fixed $25 stop was oversized at ~7x ATR. The target is now the
measured value, not an estimate.

This generator targets the statistical shape of real XAUUSD M15 instead:

  - Volatility clustering (GARCH-like): quiet stretches and violent ones,
    rather than constant sigma. This is what makes ATR-scaled targets
    behave differently from fixed ones.
  - Session structure: Asian range-bound, London expansion, London/NY
    overlap the most active, late NY winding down.
  - Trend regimes that persist for hours to days, interleaved with
    genuine ranges, so both the trend-following and mean-reversion sides
    of the strategy see the conditions they are meant to trade.
  - Occasional news spikes with a fat tail.

WHAT IT IS AND IS NOT VALID FOR
───────────────────────────────
VALID: tuning anything that depends on the *shape* of the market —
alert cadence, how fast trades resolve, whether stops are sized sanely
relative to noise, the score distribution. These are structural.

NOT VALID: any statement about edge, win rate, or expectancy. The price
path carries no real predictive structure, so profitability measured
here is noise by construction and must not be quoted.

Usage:
    python tools/make_synthetic_gold.py --bars 12000 --out gold_m15.csv
    python tools/make_synthetic_gold.py --bars 12000 --out m5.csv --minutes 5
"""
import argparse
import csv
import math
import random
from datetime import datetime, timedelta, timezone

# Target: the MEASURED median M15 ATR(14) of real XAUUSD -- $8.76 over
# 25,000 bars (tools/premise_check.py). Do not lower this on intuition:
# an earlier value of $3.5 was guesswork, and being 2.5x too quiet is
# what produced the retracted claim that a $25 stop was ~7x ATR. It is
# ~2.85x ATR. Any distance-based constant tuned on a too-quiet series is
# wrong by exactly that factor.
# Midpoint of the observed real range over the 52-week sample
# (price 3405-5586, tools/premise_check.py). The previous 2650 was stale
# by ~1.7x, which made the SAME dollar volatility look like 3.3% of spot
# per day instead of ~1.9%. Spot and target ATR must move together: ATR
# as a fraction of price is what the realism tests actually check.
DEFAULT_SPOT = 4500.0
TARGET_M15_ATR = 8.76

# Session multipliers on volatility, by UTC hour.
SESSION_VOL = {
    **{h: 0.55 for h in range(0, 7)},    # Asian: quiet, range-bound
    **{h: 1.15 for h in range(7, 13)},   # London
    **{h: 1.45 for h in range(13, 16)},  # London/NY overlap: most active
    **{h: 1.05 for h in range(16, 21)},  # NY
    **{h: 0.65 for h in range(21, 24)},  # late NY into Asia
}


def generate(bars, minutes=15, spot=DEFAULT_SPOT, seed=42,
             target_atr=TARGET_M15_ATR, start=None):
    """Return a list of OHLC candle dicts with gold-like volatility structure."""
    rnd = random.Random(seed)
    start = start or datetime(2025, 1, 6, tzinfo=timezone.utc)

    # Per-bar sigma that lands ATR(14) near the target. True range on a
    # random walk runs ~1.6x the per-bar close-to-close sigma once the
    # intrabar high/low excursion is included, and we scale by sqrt of the
    # timeframe ratio so M5/M1 series stay consistent with the M15 one.
    tf_scale = math.sqrt(minutes / 15.0)
    base_sigma = (target_atr / 1.6) * tf_scale

    # GARCH-ish variance state, trend state.
    vol_state = 1.0
    trend = 0.0
    trend_left = 0

    price = spot
    anchor = spot
    out = []
    for i in range(bars):
        t = start + timedelta(minutes=minutes * i)
        # Skip the weekend gap: gold trades ~24/5.
        if t.weekday() >= 5:
            start += timedelta(days=2)
            t = start + timedelta(minutes=minutes * i)

        # Volatility clustering: mean-reverting multiplicative state.
        vol_state = 0.94 * vol_state + 0.06 * 1.0 + rnd.gauss(0, 0.10)
        vol_state = max(0.35, min(3.0, vol_state))

        # Session overlay.
        sess = SESSION_VOL.get(t.hour, 1.0)
        sigma = base_sigma * vol_state * sess

        # Trend regimes: persist for a few hours to a couple of days.
        if trend_left <= 0:
            trend_left = rnd.randint(16, 260)
            # Two thirds of the time there is a real directional drift;
            # the rest is genuine range, which the chop filter should see.
            trend = rnd.gauss(0, 0.16) * sigma if rnd.random() < 0.66 else 0.0
        trend_left -= 1

        # Anchor pull. Without it the accumulated trend drift walks price
        # hundreds of dollars away over a long series, which inflates the
        # daily range and makes ATR-as-a-fraction-of-price meaningless.
        # Real gold trends, but around a level that moves far more slowly
        # than the intraday path. This keeps the series inside a plausible
        # band while leaving multi-day swings intact.
        anchor += rnd.gauss(0, base_sigma * 0.010)
        pull = (anchor - price) * 0.0020

        # News spike: rare, fat-tailed, and it moves price, not just range.
        if rnd.random() < 0.0018:
            sigma *= rnd.uniform(4.0, 9.0)

        o = price
        ret = trend + pull + rnd.gauss(0, sigma)
        c = o + ret
        # Intrabar excursion beyond the body, proportional to sigma.
        up = abs(rnd.gauss(0, sigma * 0.62))
        dn = abs(rnd.gauss(0, sigma * 0.62))
        h = max(o, c) + up
        l = min(o, c) - dn

        out.append({
            "t": t.isoformat(),
            "o": round(o, 2), "h": round(h, 2),
            "l": round(l, 2), "c": round(c, 2),
            "v": rnd.randint(80, 1400),
        })
        price = c
    return out


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "o", "h", "l", "c", "v"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} bars to {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bars", type=int, default=12000)
    ap.add_argument("--minutes", type=int, default=15)
    ap.add_argument("--spot", type=float, default=DEFAULT_SPOT)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--target-atr", type=float, default=TARGET_M15_ATR)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows = generate(args.bars, minutes=args.minutes, spot=args.spot,
                    seed=args.seed, target_atr=args.target_atr)
    write_csv(rows, args.out)


if __name__ == "__main__":
    main()
