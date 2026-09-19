# Gold Alert Bot — Golden Trio + SMC

Alert-only trading bot for **XAUUSD (Gold)** on Capital.com CFDs. It sends
WATCH ⚡ / A+ 🟢 alerts to Telegram. **It suggests. It never executes trades.**

## Architecture

```
                     XAUUSD
                        │
                 ┌──────┴──────┐
           Golden Trio        SMC
                 │             │
                 └──────┬──────┘
                        │
                   M15 SETUP          ← creates the opportunity
                        │
              ┌─────────┼─────────┐
             M5         H1        H4   ← modify confidence
         confirmation  context   regime
              └─────────┼─────────┘
                        │
                       M1             ← entry timing only
                        │
                      SCORE
                        │
              ┌─────────┴─────────┐
            WATCH                 A+
```

**M15 creates the opportunity. The other timeframes modify confidence;
none of them can veto it.**

### The two detectors

1. **Golden Trio** — RSI(14) momentum evidence plus 10-bar Turtle
   (Donchian) location evidence, with ZLSMA(30) slope as a separate
   signed trend-alignment axis. RSI and Turtle are *quality inputs*, not
   permission gates: there is no minimum RSI hook, no absolute level to
   cross, and no oversold veto. ZLSMA is 30 not 50 because the
   Capital.com M15 API caps at ~80 bars per request.
2. **Smart Money Concepts** — Order Block, Change of Character (CHOCH),
   and Liquidity Sweep detection via the `smartmoneyconcepts` library.

Each scan runs both and keeps the higher-quality candidate. Both report
quality on the **same 0..1 axis** against the same point budget, and both
are measured on the same ZLSMA and chop axes — so the comparison between
them is structural rather than an arbitrary rescale of two unrelated
point totals.

### Scoring

| Group | Components | Range |
|---|---|---|
| Setup (M15) | `setup_quality × SCORE_SETUP_MAX` | 0 … +45 |
| | ZLSMA / structure alignment | −12 … +15 |
| MTF | H4 regime | −12 … +12 |
| | H1 context (range location + EMA slope) | −10 … +10 |
| | M5 confirmation (displacement, RSI slope, participation) | −10 … +10 |
| | M1 entry timing (pressure, chase penalty) | −5 … +5 |
| Context | round number / chop / ATR regime | −20 … +5 |

Tiers are a **pure function of the score**: WATCH at ≥ 45, A+ at ≥ 70.
There are no post-score vetoes — an unfavourable H4 regime, a flat or
opposing ZLSMA and a chop regime all cost points, so the system can never
say "this is a 75-point setup" and then refuse to call it A+.

Each MTF contribution is **zero-centred**: a timeframe that is neutral
and a timeframe whose candles are unavailable both contribute exactly 0.
That is what lets an M15-only backtest and a full-MTF live run sit on the
same score scale instead of differing by a constant offset.

**Targets.** Sized in ATR, because gold's volatility varies far more than
a fixed dollar distance can absorb:

- `TARGET_MODE = "ATR"` (default): stop at 3×ATR, ladder 1R / 2R / 3.5R.
- `TARGET_MODE = "FIXED"`: the legacy $25 / $25 / $50 / $100 ladder, kept
  so the two can be compared on identical candles.

3×ATR on **real** gold is a $26.29 stop, against a measured median M15
ATR of **$8.76** (`tools/premise_check.py`, 25,000 real bars):

| spread | as % of a 3.0×ATR ($26.29) stop |
|---|---|
| $0.30 | 1.1% |
| $0.75 (realistic) | 2.9% |
| $1.50 (conservative) | 5.7% |

### RETRACTED: "the old $25 stop was ~7×ATR and that is why the bot was silent"

An earlier version of this file carried a table derived from the
synthetic generator, which produced an M15 ATR of ~$3.5. Real gold's
median M15 ATR is **$8.76** — the generator was about 2.5× too quiet, so
every ATR multiple computed from it was inflated by the same factor.

Corrected against real data:

| claim | as published | measured |
|---|---|---|
| $25 fixed stop, in ATR | ~7×ATR | **~2.85×ATR** |
| spread as %R at 3.0×ATR | 7.2% | **2.9%** |

**The $25 fixed stop was approximately the right size.** It sits within
5% of what `ATR_SL_MULT = 3.0` produces on real candles. The criticism
of it was an artifact of the generator, and the claim that stop size —
rather than the thresholds — was what made the bot silent is not
supported by anything measured on real data.

`TARGET_MODE = "ATR"` is still the better default, because it tracks
volatility instead of assuming it. But it is not the large correction
this file previously claimed it was, and the cadence improvement
attributed to it should be re-measured before it is believed.

**Entry.** On a limit `ENTRY_PULLBACK_ATR` (0.5) better than the trigger
bar's close, never at the close itself. This corrects a structural
anti-edge rather than refining one — see below.

**Alert cadence.** Thresholds are derived from a target delivered rate by
`tools/tune_thresholds.py`, not chosen by hand: **WATCH ≥ 45, A+ ≥ 65**
(`strategy_config.py`). They must be re-derived whenever the score
budget or entry logic changes.

A+ is a **cadence** tier, not a quality tier — above 45 the score barely
orders outcomes (see Real-data results). Do not read A+ as "this one is
more likely to win".

## What the no-edge control test found

The most useful result here came from running the strategy against a
**driftless** series — same volatility, trend structure removed — where
no entry can beat 50% because there is no directional information to
find. Anything that deviates from 50% is geometry, not skill.

Two things showed up that no amount of backtesting on trending data would
have separated from noise:

**1. The entry was on the wrong side of the bar.** A symmetric ±1R
barrier race from each signal:

```
random bars, random direction        50.2%   ← correct baseline
strategy entries, own direction      43.7%
strategy entries, direction FLIPPED  56.3%
```

A BUY triggers on a bar that hooked up off its low, so that bar's close
sits near its high — entering there starts every trade nearer its stop
than its target. Waiting for a 0.5 ATR pullback restores 50.2% exactly,
at the cost of fill rate (72% vs 95%). An unfilled setup costs nothing; a
structurally disadvantaged fill costs on every one taken.

**2. The exit ladder needs a real edge to break even.** With 50/30/20
partial exits and a move to breakeven after TP1, the typical winner banks
≈ +0.68R while the typical loser costs −1.00R:

```
BREAKEVEN WIN RATE REQUIRED = 59%
```

That is the bar any genuine signal has to clear. It is a property of the
exit design, not of any dataset, and it is why *expectancy* — not TP1 hit
rate — is the number that decides whether this is worth trading.

## Real-data results (52 weeks, XAUUSD M15)

Two runs of the backtest workflow against live Capital.com history. The
second is the one that matters: 25,000 M15 bars, 2025-08-29 → 2026-09-11,
ATR mode, on the recalibrated config.

```
n=2039 filled   53% WR   +0.039R ±0.046   pf 1.08   (+80.1R total)

ideal spread        +0.08R   pf 1.17
realistic ($0.75)   +0.04R   pf 1.08
conservative ($1.50) +0.00R  pf 1.00
```

**Read that as approximately breakeven, not as profitable.** Three
reasons, all visible in the same run:

1. **±0.046 on a +0.039R mean is 0.85 sigma.** Not distinguishable from
   zero. It is not a result, it is a direction.
2. **At the conservative spread it is exactly 0.00R.** The whole apparent
   edge is smaller than the difference between two plausible spread
   assumptions.
3. **It is entirely one-directional:**
   ```
   BUY   n=1177  56% WR  +0.12R  pf 1.28
   SELL  n= 862  48% WR  -0.08R  pf 0.85
   ```
   Gold trended up across most of this window, so a long bias earns
   money without any edge being present. Until a run covers a sustained
   gold *downtrend*, the BUY column cannot be separated from beta.

### What replicated, and what did not

The 26-week run was used to pick the current settings; the 52-week run
reaches back to Aug 2025, which that choice never saw. Held up:

| finding | 26w | 52w |
|---|---|---|
| `h4_confirm` is the strongest component | Δ +0.131R | **Δ +0.208R** |
| `h4_neutral` / `h4_against` hurt | −0.188 / −0.031 | **−0.237 / −0.116** |
| Asian session underperforms | Δ −0.065R | **Δ −0.065R** |
| `round_number` bonus was backwards | Δ −0.066R | **Δ −0.089R** |

Did **not** replicate: `zlsma_flat` measured Δ −0.226R on 26 weeks and
Δ −0.009R on 52. A fitted finding that evaporated — which is why the
component table exists.

### The threshold change was curve-fitting, and was reverted

`WATCH_MIN_SCORE` was moved 45 → 55 because the 26-week run showed the
45-54 band losing money in both of that run's halves. But that run used
the new threshold, so it contained no sub-threshold signals and could not
test the change. A 52-week run with `--record-all` could:

```
           0-44              45-54             55-64
older     -0.102R ±0.024    +0.053R ±0.049    +0.057R ±0.063
recent    -0.018R ±0.037    -0.068R ±0.077    +0.005R ±0.098
```

The 45-54 band was **positive** over the older period — the part the
choice had never seen — and indistinguishable there from the 55-64 band
that was kept. It was negative only inside the six-month window used to
pick 55. **Reverted to 45.** Excluding it discarded ~2,470 signals worth
roughly +47R on the strength of one sample.

The same table does establish a real floor, just a lower one: **0-44 is
negative in both halves and 4+ sigma negative in the larger (n=7060)**.
That is the only threshold finding that has replicated.

### The score barely orders outcomes above 45

Full sample: 45-54 +0.019R, 55-64 +0.042R, 65-74 +0.032R. The
calibration verdict has been NOT MONOTONIC on every run. A+ (65+) is a
**cadence tier, not a quality tier**, and must not be presented as
higher-conviction.

### RETRACTED: "the score works for SMC but not Golden Trio"

An earlier version of this file recommended rebuilding around SMC on the
strength of this:

```
                  55-64      65-74
CHOCH_REVERSAL   +0.022R   +0.228R
GOLDEN_TRIO      +0.045R   -0.017R
```

**That recommendation does not hold.** The +0.228R rests on n=97 at
±0.211 — about 1.1 sigma — and the component marginal flips sign with
the measured population:

| run | `smc_choch` marginal |
|---|---|
| above-threshold signals only | **+0.067R** |
| all scored candidates | **−0.044R** |

There is no reliable evidence that either detector's score is better
than the other's. Both are close to noise above 45. Do not rebuild
around SMC on the basis of this data.

### What replicated across runs

| finding | status |
|---|---|
| `0-44` band unprofitable | **replicated**, largest effect in the data |
| `h4_confirm` strongest component | **replicated** (+0.131 → +0.208 → +0.147R) |
| `h4_against` / `h4_neutral` hurt | **replicated** |
| `round_number` bonus backwards | **replicated 3×** (−0.066 / −0.089 / −0.088R) |
| `WATCH_MIN` 55 better than 45 | **FALSIFIED** — reverted |
| `zlsma_flat` harmful | **failed** (−0.226R → −0.009R) |
| Asian session harmful | **ambiguous** (−0.065R → +0.019R, confounded populations) |
| SMC better than Golden Trio | **flipped sign** (+0.067R → −0.044R) |

Three of the four component changes made after the first real run rest
on evidence that either failed or is now in doubt. The config records
which is which.

## Do the strategy's premises hold? (`tools/premise_check.py`)

Every tuning round in this project fitted parameters and then watched the
finding evaporate on wider data. This asks a different question, one that
cannot be curve-fitted: **are the assumptions underneath the strategy
true of gold at all?** Each test is parameter-free, and each hypothesis
comes from outside this dataset — published literature, or the
strategy's own stated logic.

Run on 25,000 real M15 bars. Read |t| > 3 as interesting, |t| > 2 as
suggestive, and remember that several hypotheses are tested at once.

### 1. The Turtle-band bounce premise is NOT supported

Entry requires price at a Donchian extreme, on the assumption it bounces.
Measured directly — the next n-bar return after touching an n-bar extreme:

| period | after n-bar LOW | after n-bar HIGH |
|---|---|---|
| 10 | +0.485 (t=+1.2, n=3984) | **+0.883 (t=+2.4, n=4763)** |
| 20 | +1.605 (t=+2.1, n=2487) | +1.177 (t=+2.0, n=3308) |

The bounce premise requires *positive* after a low and *negative* after a
high. After a 10-bar **high**, gold goes **up** — significantly. At 20
bars both directions are positive. Selling the top of the band is
pointed the wrong way on this data.

### 2. Momentum after up-moves, not reversion

Conditional next-k-bar move, given the prior k-bar move:

| k | after UP move | after DOWN move |
|---|---|---|
| 2 | +0.121 (t=+1.2) | +0.031 (t=+0.3) |
| 4 | +0.291 (t=+2.1) | +0.009 (t=+0.1) |
| 8 | +0.570 (t=+2.8) | +0.030 (t=+0.1) |
| 16 | **+0.913 (t=+3.4)** | +0.310 (t=+1.0) |

Up-moves **continue**, and more strongly at longer horizons. Down-moves
do not revert. This is the strongest statistical signal anywhere in this
repository — and it is the opposite of what a dip-buying, rally-selling
entry needs.

It also independently reproduces the BUY +0.12R vs SELL −0.08R asymmetry
seen in the backtest, using a test that involves none of the strategy's
code. Note the overlap with gold's uptrend over this sample: the effect
may be a bull-market artifact.

Lag-by-lag autocorrelation disagrees, showing mild *reversion* at lags 3
and 4 only (t=−3.6, −2.7) and nothing elsewhere. The two tests point
opposite ways, so the reversion signal is weak and horizon-specific
while the momentum signal is the more robust of the two.

### 3. Session drift: only 21–24 UTC

| session | mean/bar | t | n | |
|---|---|---|---|---|
| asian 00-07 | +0.0593 | +0.6 | 7643 | not significant |
| london 07-13 | +0.0503 | +0.5 | 6551 | not significant |
| overlap 13-16 | −0.1872 | −1.0 | 3275 | not significant |
| ny 16-21 | −0.0602 | −0.6 | 5367 | not significant |
| **late 21-24** | **+0.5141** | **+2.7** | 2159 | **significant positive** |

The widely-repeated "gold rises in Asia, falls in London" claim is
blog-sourced, not peer-reviewed, and is **not** reproduced here. The one
significant window is a different one.

### What this means

The Golden Trio's core reasoning — price is stretched, expect a reversion
— is the part the data pushes back on hardest. That is a **sign error in
the core logic, not a parameter problem**, and no threshold round can
reach it. The indicated next experiment is a momentum/continuation entry
tested head-to-head against the current one on identical candles.

That experiment has **not** been run. Nothing above should be read as
evidence that an inverted strategy would be profitable — only that the
current one is aimed against the one effect this data supports.

### Literature

The published gold edge is at **12-month** horizons (Moskowitz, Ooi &
Pedersen, time-series momentum), with monthly holding periods — nowhere
near M15. Intraday gold research concerns **volatility structure**, not
directional returns (Batten & Lucey; Iwatsubo, Watkins & Xu on Tokyo vs
New York sessions). Across intraday commodity work the consistent
finding is that **transaction costs are what kill short-horizon edges**,
which matches this strategy's own decay from ideal +0.08R to
conservative +0.00R.

There is no published, validated M15 gold strategy to copy.

## Validation status

**This is a rule-based prototype, not a validated strategy.** The
repository contains the machinery to measure an edge and, as of the
control test above, evidence about its *structure*. It does not contain a
demonstrated edge, and nothing here is a profitability claim.

- No real market data has been run through it. All numbers above come
  from `tools/make_synthetic_gold.py`, which reproduces gold's
  volatility, session profile and trend/range alternation but carries no
  genuine predictive structure.
- Results on the trending synthetic series look positive. **Ignore them.**
  That generator has trend regimes built in, and a trend-aware strategy
  will rediscover them; it is measuring the generator, not the market.
  The driftless control is the honest read, and there the strategy is
  negative — as anything must be on data with no edge.
- The thresholds set cadence, not quality. Whether a score of X predicts
  anything is untested; `tools/calibrate_scores.py` is what answers it,
  and only on real history.

What synthetic data *can* settle is structure: stop sizing relative to
noise, how fast trades resolve, alert cadence, and the two control-test
findings above. Those transfer. Expectancy does not.

### Backtesting

```bash
python backtest.py --candles XAUUSD_M15.csv --json signals.json
python backtest.py --candles XAUUSD_M15.csv --m5 M5.csv --m1 M1.csv
python backtest.py --candles XAUUSD_M15.csv --target-mode ATR
```

The backtester replays the live code paths (same detectors, same scorer,
same MTF layer, same cooldown logic) so live and backtest cannot drift.
H1/H4 are aggregated from M15 with the trailing partial bar dropped, so
no unclosed higher-timeframe candle is ever visible. M5 and M1 are *not*
synthesised from M15 — pass real CSVs to exercise those layers, or they
score neutral.

It reports **two different rates**, because this is an alert bot and not
a one-position execution engine:

- **Signal opportunity rate** — every setup that cleared the score
  threshold. This measures the strategy.
- **Tradeable alert rate** — what survived the cooldown and
  one-position-at-a-time gates. This measures what you would have
  received.

Conflating them lets a position gate silently shrink the sample. Both are
simulated under three cost regimes (ideal / $0.75 / $1.50 spread) with
partial exits weighted 50% at TP1, 30% at TP2, 20% at TP3 — which is why
**expectancy, not win rate, is the number that matters**: a high TP1 hit
rate is compatible with mediocre expectancy.

### Score calibration

```bash
python tools/calibrate_scores.py --signals signals.json --oos-split 0.7
```

Reports realized expectancy per score bucket with error bars, the same
table split by detector, an in-sample/out-of-sample walk-forward split,
and the marginal expectancy of each score component. It flags
under-powered buckets and refuses to call a score informative on a sample
too small to say so.

## How to run it (real-time mode — recommended)

The bot is designed to run as one always-on process:

```
python run_forever.py
```

This scans exactly at :00/:15/:30/:45 UTC and, between scans, listens to your
Telegram messages in real time:

| Command | What it does |
|---|---|
| `/scan` | run a full scan right now and get the read |
| `/status` | active WATCHes, pending A+ confirmations, last scan time |
| `/help` | command menu |

### Option A — run it on GitHub Actions for free (relay mode)

Because this repo is **public**, Actions minutes are free and unlimited, so
the workflow runs the bot in "relay" mode: each job keeps `run_forever.py`
alive for ~5h40m (GitHub kills jobs at 6h), then dispatches the next job
before exiting, using the run's own automatic token — **no token to create,
no extra secret to add.** All required secrets (`CAPITAL_API_KEY`,
`CAPITAL_EMAIL`, `CAPITAL_PASSWORD`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`) are already configured on this repo. Setup is just:

1. Actions tab → market-expert-bot → **Run workflow** — that's it, the
   chain keeps itself alive from there.

If the "Chain the next relay run" step ever shows a ⚠️ warning in the logs,
this repo's default Actions token permissions are set to read-only, which
blocks self-dispatch. The one-time fix: repo Settings → Actions → General →
scroll to **Workflow permissions** → select **Read and write permissions**
→ Save. No token to generate — it's a single radio button. Until that's
flipped, the hourly cron below still restarts the bot automatically
(just with up to a ~1h gap instead of an immediate handoff).

Known trade-offs of relay mode:

- normally a ~1–3 min blind spot every ~5h40m while jobs hand off; if
  self-dispatch isn't permitted (see above), the gap is up to ~1h until
  the hourly cron catches it instead
- GitHub's terms discourage using Actions as generic always-on compute;
  small bots usually fly under the radar, but GitHub may disable the
  workflow — if that happens, switch to Option B
- the repo must stay **public** (private repos get only 2,000 free
  minutes/month — a day and a half of relay), so never commit secrets

### Option B — any always-on host (Render, Railway, VPS, old laptop)

No gaps, no terms-of-service gray area. In Render: **New → Background
Worker → connect this repo** (it reads `render.yaml` automatically), set
the same env vars, deploy. Or on any machine you own that stays on:
set the env vars and run `python run_forever.py` under
`systemd`/`tmux`. If a host is running the bot, disable the Actions
workflow (Actions → market-expert-bot → ⋯ → Disable workflow) so you
don't get duplicate alerts.

## Environment variables

Never commit these. Set them on the host (Render dashboard, or a local
`.env` you keep out of git).

```
CAPITAL_API_KEY=...
CAPITAL_EMAIL=...
CAPITAL_PASSWORD=...
CAPITAL_BASE_URL=https://demo-api-capital.backend-capital.com/api/v1
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## Tests

```
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

## Before real money

Paper-trade the first 30 suggestions, log them, and go live only if
profitable, at 2% position size.
