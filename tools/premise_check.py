"""
Test the PREMISES the strategy rests on, not its parameters.

Every tuning round in this project fitted parameters and then watched the
finding evaporate on wider data. This script does something different: it
asks whether the assumptions underneath the strategy are true of gold at
all. Each test is parameter-free or nearly so, and each hypothesis comes
from OUTSIDE this dataset (published literature or the strategy's own
stated logic), so testing it here is not curve-fitting.

  1. MEAN REVERSION vs MOMENTUM at M15.
     Golden Trio buys dips and sells rallies. That is only sensible if
     gold mean-reverts at this horizon. Measured by return
     autocorrelation and by conditional next-move after an n-bar move.
     Negative autocorrelation = reverting = premise holds.
     Positive = momentum = the strategy is structurally backwards.

  2. THE TURTLE-BAND PREMISE.
     The entry requires price near a 10-bar Donchian extreme, assuming a
     bounce. Directly measured: after touching an n-bar low, is the next
     n-bar return positive?

  3. SESSION DIRECTIONAL DRIFT.
     A widely-repeated claim (blog-sourced, not peer-reviewed) says gold
     rises in Asia and falls in London/NY. If true, a direction-agnostic
     strategy fights a real drift, and a directional session bias would
     be a simpler edge than anything here.

  4. THE COST BARRIER.
     Published work on scalping finds transaction costs are what kills
     short-horizon edges. Quantify: what edge must be found to clear the
     spread at this holding period?

Usage:
    python tools/premise_check.py --candles gold_m15.csv
"""
import argparse
import math
import statistics

import pandas as pd


def tstat(xs):
    xs = [x for x in xs if x is not None and not math.isnan(x)]
    if len(xs) < 3:
        return 0.0, 0.0, 0
    m = statistics.mean(xs)
    sd = statistics.stdev(xs)
    if sd == 0:
        return m, 0.0, len(xs)
    return m, m / (sd / math.sqrt(len(xs))), len(xs)


def verdict(t, threshold=2.0):
    if abs(t) < threshold:
        return "not significant"
    return "SIGNIFICANT " + ("positive" if t > 0 else "negative")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles", required=True)
    a = ap.parse_args()

    df = pd.read_csv(a.candles)
    df["t"] = pd.to_datetime(df["t"], utc=True, format="mixed")
    df = df.sort_values("t").reset_index(drop=True)
    c = df["c"].astype(float).values
    h = df["h"].astype(float).values
    l = df["l"].astype(float).values
    n = len(df)
    ret = [0.0] + [c[i] - c[i - 1] for i in range(1, n)]
    print(f"{n} M15 bars  {df['t'].iloc[0]} -> {df['t'].iloc[-1]}")
    print(f"price {c.min():.0f}-{c.max():.0f}\n")

    # ── 1. reversion vs momentum ────────────────────────────────────
    print("1. MEAN REVERSION vs MOMENTUM AT M15")
    print("   Golden Trio assumes reversion. Negative = reverting (premise holds).")
    print(f"   {'lag':>5} {'autocorr':>10} {'t':>8}   verdict")
    s = pd.Series(ret[1:])
    for lag in (1, 2, 3, 4, 6, 8):
        r = s.autocorr(lag=lag)
        t = r * math.sqrt(len(s) - lag)          # approx under H0: rho=0
        print(f"   {lag:>5} {r:>+10.4f} {t:>+8.1f}   {verdict(t)}")

    print("\n   Conditional: after a k-bar move, the NEXT k-bar move")
    print(f"   {'k':>4} {'after UP move':>22} {'after DOWN move':>22}")
    for k in (2, 4, 8, 16):
        up, dn = [], []
        for i in range(k, n - k):
            prev = c[i] - c[i - k]
            nxt = c[i + k] - c[i]
            (up if prev > 0 else dn).append(nxt)
        mu, tu, nu = tstat(up)
        md, td, nd = tstat(dn)
        print(f"   {k:>4}   {mu:>+8.3f} t={tu:>+5.1f} n={nu:<6}  {md:>+8.3f} t={td:>+5.1f} n={nd:<6}")
    print("   Reversion premise -> after UP should be NEGATIVE, after DOWN POSITIVE.")

    # ── 2. Turtle band premise ──────────────────────────────────────
    print("\n2. THE TURTLE-BAND PREMISE (bounce off an n-bar extreme)")
    print(f"   {'period':>7} {'after n-bar LOW':>26} {'after n-bar HIGH':>26}")
    for period in (10, 20):
        lows, highs = [], []
        for i in range(period, n - period):
            win_lo = l[i - period + 1:i + 1].min()
            win_hi = h[i - period + 1:i + 1].max()
            fwd = c[i + period] - c[i]
            if l[i] <= win_lo + 1e-9:
                lows.append(fwd)
            if h[i] >= win_hi - 1e-9:
                highs.append(fwd)
        ml, tl, nl = tstat(lows)
        mh, th, nh = tstat(highs)
        print(f"   {period:>7}   {ml:>+8.3f} t={tl:>+5.1f} n={nl:<6}  {mh:>+8.3f} t={th:>+5.1f} n={nh:<6}")
    print("   Bounce premise -> after a LOW should be POSITIVE, after a HIGH NEGATIVE.")

    # ── 3. session drift ────────────────────────────────────────────
    print("\n3. SESSION DIRECTIONAL DRIFT (literature: up in Asia, down in London/NY)")
    df["hr"] = df["t"].dt.hour
    df["ret"] = ret
    print(f"   {'session':<18} {'mean $/bar':>11} {'t':>7} {'bars':>7}   verdict")
    for name, lo, hi in [("asian 00-07", 0, 7), ("london 07-13", 7, 13),
                         ("overlap 13-16", 13, 16), ("ny 16-21", 16, 21),
                         ("late 21-24", 21, 24)]:
        xs = df[(df.hr >= lo) & (df.hr < hi)]["ret"].tolist()[1:]
        m, t, cnt = tstat(xs)
        print(f"   {name:<18} {m:>+11.4f} {t:>+7.1f} {cnt:>7}   {verdict(t)}")

    # ── 4. cost barrier ─────────────────────────────────────────────
    print("\n4. THE COST BARRIER")
    tr = pd.concat([df["h"] - df["l"],
                    (df["h"] - df["c"].shift(1)).abs(),
                    (df["l"] - df["c"].shift(1)).abs()], axis=1).max(axis=1)
    atr_med = tr.ewm(alpha=1 / 14, adjust=False).mean().median()
    print(f"   median M15 ATR: ${atr_med:.2f}")
    for spread in (0.30, 0.75, 1.50):
        for mult in (3.0,):
            stop = mult * atr_med
            print(f"   spread ${spread:.2f} vs a {mult}xATR (${stop:.2f}) stop"
                  f"  -> costs {100 * spread / stop:.1f}% of R per trade")
    print("   A strategy must beat that per trade before it earns anything.")
    print("\nNOTE: many tests above; treat |t|>3 as interesting and |t|>2 as suggestive.")


if __name__ == "__main__":
    main()
