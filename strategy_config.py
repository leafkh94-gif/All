"""
Gold-only trading alert bot — configuration.
Single instrument (XAUUSD), single strategy (Golden Trio: Turtle+RSI+ZLSMA).
"""

# ─────────────────────────────────────────────────────────────────────
# 1.1  Instrument
# ─────────────────────────────────────────────────────────────────────
INSTRUMENTS = {
    # "Gold" alone matched a stale/non-primary feed (last candle ~48h old).
    # "XAUUSD" is Capital.com's canonical symbol for spot gold and returns
    # the live tradeable epic.
    "XAUUSD": {"name": "Gold", "search": "XAUUSD", "class": "COMMODITY"},
}
ACTIVE_INSTRUMENTS = ["XAUUSD"]

# ─────────────────────────────────────────────────────────────────────
# 1.2  Architecture
# ─────────────────────────────────────────────────────────────────────
SCAN_INTERVAL_MINUTES = 15

# ─────────────────────────────────────────────────────────────────────
# 1.2b  Target ladder
# ─────────────────────────────────────────────────────────────────────
# TARGET_MODE selects how stop/TP distances are built:
#   "ATR"        (default) — distances scale with current M15 volatility
#   "FIXED"      — fixed dollar distances, retained for comparison
#   "STRUCTURAL" — Turtle-band-derived (golden_trio only)
#
# WHY ATR IS THE DEFAULT
# ──────────────────────
# The previous default was a fixed $25 stop with $25/$50/$100 targets.
# Measured against a volatility-realistic gold series (M15 ATR ~$3.5,
# daily range ~1.45% of spot — see tools/make_synthetic_gold.py), that
# ladder is:
#
#   $25 stop  ≈ 7x M15 ATR    median 71 bars (~18h) to resolve;
#                             only 60% resolve inside one trading day
#   $100 TP3  ≈ 29x M15 ATR   effectively unreachable on M15
#
# A stop that takes most of a day to resolve is not a scalp, and while it
# sits open it blocks every later setup — which is what made the bot feel
# silent. The fixed ladder is not deleted, just no longer the default, so
# the two remain comparable on identical candles via
# `backtest.py --target-mode FIXED`.
#
# HOW ATR_SL_MULT WAS CHOSEN
# ──────────────────────────
# Two forces pull opposite ways; 3.0x is where they balance:
#
#   xATR  stop$   spread as %R   resolve <=1 day   median bars
#   1.5    5.24      14.3%            100%              5
#   2.0    6.98      10.7%            100%              9
#   3.0   10.47       7.2%             97%             17   <- chosen
#   4.0   13.96       5.4%             92%             28
#   8.0   27.92       2.7%             60%             71   <- the old $25
#
# Tighter than 3x and the spread eats a punishing share of every trade
# (gold's spread is large relative to M15 noise). Wider and trades stop
# resolving inside a session. 3.0x costs ~7% of R in spread at the
# realistic spread and resolves in ~4 hours at the median.
TARGET_MODE = "ATR"
POINT_VALUE = 1.0

# ATR ladder. Within a one-day hold on realistic volatility, price reaches
# 1R 97% of the time, 2R 75%, 3.5R 42%. The old $100 TP3 essentially never
# printed, which is why TP2/TP3 hit rates read 0% in earlier runs.
ATR_SL_MULT = 3.0
ATR_TP1_R = 1.0
ATR_TP2_R = 2.0
ATR_TP3_R = 3.5
# Bound the stop so a dead tape can't put it inside the spread and a news
# spike can't produce an absurd distance. At the p10/p90 of realistic M15
# ATR ($2.2/$5.4) the raw 3x stop is $6.6/$16.2, so these bite only in
# genuine extremes.
ATR_SL_MIN_POINTS = 7.0
ATR_SL_MAX_POINTS = 20.0
ATR_TARGET_PERIOD = 14

# ENTRY PLACEMENT
# ───────────────
# Enter on a limit a fraction of an ATR BETTER than the trigger bar's
# close, rather than at the close itself.
#
# This is not a refinement, it corrects a structural anti-edge. Measured
# on a driftless series -- data containing no directional information at
# all, where any entry must score 50% -- the symmetric +/-1R barrier race
# from each Golden Trio signal came out:
#
#   entry price                        filled   win%
#   close of the trigger bar             95%    41.6%   <- was
#   midpoint of the trigger bar          88%    45.2%
#   trigger bar extreme (low/high)       63%    46.4%
#   0.5 ATR better than the close        72%    50.2%   <- chosen
#
# The cause is positional, not predictive. A BUY triggers on a bar that
# hooked up off its low, so its close sits near that bar's high: the
# entry is taken at the top of the move that produced the signal. That
# starts the trade closer to its stop than to its target in practice, and
# it cost about 8 points of win rate before any market edge was involved.
#
# Requiring a small pullback removes the bias exactly (50.2% vs a 50.0%
# baseline). The price is fill rate: 72% instead of 95%, so roughly a
# quarter of setups never fill. That is the right trade -- an unfilled
# setup costs nothing, while a structurally disadvantaged fill costs real
# money on every one taken.
#
# Because this was measured where no edge can exist, it is a property of
# the geometry, not a pattern fitted to a particular price path.
ENTRY_PULLBACK_ATR = 0.5

# Legacy fixed ladder — kept so --target-mode FIXED still works.
FIXED_SL_POINTS = 25
FIXED_TP1_POINTS = 25
FIXED_TP2_POINTS = 50
FIXED_TP3_POINTS = 100

# Was 1.5, which was 6% of a $25 stop but is 14% of the ~$10.5 ATR stop.
# 1.0 keeps the worst accepted spread under ~10% of risk.
MAX_SPREAD_POINTS = 1.0

# ─────────────────────────────────────────────────────────────────────
# 1.3  Round-number levels (gold trades in 50-dollar increments; 3 pts proximity)
# ─────────────────────────────────────────────────────────────────────
ROUND_NUMBER_BONUS = 5
ROUND_NUMBER_OFFSET_TABLE = {
    "XAUUSD": (50, 3),
}

# ─────────────────────────────────────────────────────────────────────
# 1.4  Alert thresholds -- score starts at 0 now (no base-60 crutch); every
# component has to earn its points, so these thresholds mean "actual signal
# quality" instead of "cleared the artificial floor".
# ─────────────────────────────────────────────────────────────────────
# Thresholds are DERIVED, not guessed. They come from
# tools/tune_thresholds.py against a volatility-realistic series, by
# targeting a delivered alert cadence rather than picking a number that
# "feels" selective.
#
# The previous A+ = 70 fired on 4 of 573 signals (0.7%) against a
# practical score ceiling of 76 — silent. It had been carried unchanged
# through a scoring rewrite that changed what the score means, which is
# how a threshold quietly stops matching its distribution.
#
# Measured delivered rates (exact: these split alerts that genuinely
# fired, after the cooldown and one-position gates):
#
#   A+ line   A+/wk   WATCH/wk        A+ line   A+/wk   WATCH/wk
#      48      6.2       2.9             58      1.4       7.7
#      50      4.8       4.3             60      0.9       8.2
#      52      3.7       5.4             65      0.4       8.7
#      55      2.4       6.7  <-         70      0.0       9.1  <- old
#
# 55 puts A+ at ~1 every 2-3 days and WATCH at ~1.4/day, roughly a
# 26/74 split of a ~9/week total. Selective without being silent.
#
# Note the total delivered rate is ~9/week almost regardless of the A+
# line: the binding constraint is the one-position gate, not the
# threshold. Moving A+ mostly re-labels alerts between tiers. That is why
# the ladder and hold-window fixes mattered far more than this number —
# they took the delivered rate from 1.9/week to 9.1/week on their own.
NO_ALERT_MAX = 44
WATCH_MIN_SCORE = 45
WATCH_MAX_SCORE = 54
APLUS_MIN_SCORE = 55

# ─────────────────────────────────────────────────────────────────────
# Score budget.
#
# The score is built from three groups, all on one 0..100 scale:
#
#   A. M15 SETUP        setup_quality * SCORE_SETUP_MAX          0..45
#      + trend-alignment axis (ZLSMA for GT, structure for SMC)  -12..+15
#   B. MTF LAYER        H4 regime / H1 context / M5 confirm /
#                       M1 timing, each signed and bounded       -37..+37
#   C. CONTEXT          round number, chop, ATR regime           -20..+5
#
# Max realistically ≈ 100 (clamped). Note group B is *zero-centred*: a
# timeframe with no candles available contributes exactly 0, identical to
# a neutral reading, so an M15-only backtest and a full-MTF live run
# produce comparable scores instead of a systematic offset.
# ─────────────────────────────────────────────────────────────────────

# A. Setup quality. ONE budget shared by both detectors. Each detector
# reports setup_quality as a 0..1 fraction of its own internal maximum,
# so Golden Trio and SMC land on the same axis by construction rather
# than by an arbitrary rescale of two unrelated point totals.
# NOTE: equal *range* is not equal *meaning* -- whether GT 0.8 and SMC
# 0.8 carry the same expectancy is an empirical question. Measure it with
# tools/calibrate_scores.py; do not assume it.
SCORE_SETUP_MAX = 45

# Per-detector internal weighting of the components that make up
# setup_quality (must sum to 1.0 within each detector).
GT_QUALITY_WEIGHT_RSI = 0.6      # RSI is evidence of momentum...
GT_QUALITY_WEIGHT_TURTLE = 0.4   # ...Turtle is evidence of location.

# Legacy component ceilings. Still exported so the detectors can report
# per-component points for the alert breakdown, but they no longer set
# the score: setup_quality does.
SCORE_RSI_CONFIRM_MAX = 30
SCORE_TURTLE_MAX = 20

# Trend-alignment axis. GT reads it off ZLSMA slope; SMC reads it off the
# structural bias the pattern implies. Same points either way.
SCORE_ZLSMA_ALIGNED = 15        # slope/structure aligned with the entry
SCORE_ZLSMA_FLAT = 0            # flat contributes nothing
SCORE_ZLSMA_AGAINST = -12       # slope/structure opposes the entry

# B. MTF layer budgets. Each is the magnitude of a signed contribution:
# a timeframe fully confirming earns +MAX, fully contradicting -MAX.
SCORE_H4_MAX = 12               # regime
SCORE_H1_MAX = 10               # context
SCORE_M5_MAX = 10               # confirmation
SCORE_M1_MAX = 5                # entry timing only -- smallest by design

# Back-compat aliases. htf_bias()/older callers still reference these.
SCORE_H4_ALIGNED = SCORE_H4_MAX
SCORE_H4_FLAT = 0
SCORE_H4_OPPOSED = -SCORE_H4_MAX

SCORE_KILLZONE_MAX = 0          # was 10; the bonus concentrated alerts
                                # into the London/NY overlap and left the
                                # rest of the day artificially short of
                                # threshold. Gold trades 24/5 -- alerts
                                # should reflect actual setup quality,
                                # not what time it is.

# C. Context.
SCORE_ROUND_NUMBER = 5
SCORE_ATR_SWEET_SPOT_PENALTY = -10
SCORE_CHOP_PENALTY = -10        # recent range is compressed (chop regime)

# ─────────────────────────────────────────────────────────────────────
# MTF layer tuning (strategy/mtf.py)
#
# Every "FULL_*" constant is the reading at which that component's
# contribution saturates at ±1.0. Readings scale linearly up to it, so a
# marginal trend earns marginal points instead of the old all-or-nothing
# ±15 on H4.
# ─────────────────────────────────────────────────────────────────────
MTF_H4_EMA_PERIOD = 20
MTF_H4_FLAT_BAND_PCT = 0.001    # |EMA slope| below this -> regime FLAT
MTF_H4_FULL_SLOPE_PCT = 0.006   # 0.6% EMA(20) move over 5 H4 bars = full weight

MTF_H1_RANGE_BARS = 24          # one trading day of H1 for the location read
MTF_H1_EMA_PERIOD = 20
MTF_H1_SLOPE_BARS = 6
MTF_H1_FULL_SLOPE_PCT = 0.004

MTF_M5_LOOKBACK = 6             # 30 minutes of M5
MTF_M5_RSI_PERIOD = 14
MTF_M5_FULL_DISP_ATR = 1.5      # 1.5 ATR of net displacement = full weight
MTF_M5_FULL_RSI_DELTA = 12.0    # 12 RSI points of slope = full weight

MTF_M1_LOOKBACK = 10            # 10 minutes
MTF_M1_FULL_EXTENSION_ATR = 3.0 # already 3 M1-ATR past entry = max chase penalty

# How many bars of each timeframe the live feed pulls per scan.
MTF_FETCH_BARS = {"1min": 120, "5min": 200, "15min": 160, "1h": 160, "4h": 260}

DAILY_LOSS_LIMIT_USD = 20.0
DAILY_LOSS_BREAKER_DURATION_DAYS = 14

# ─────────────────────────────────────────────────────────────────────
# 1.5  Trade lifecycle (used by ActiveEntryTracker / OpenTradeTracker)
# ─────────────────────────────────────────────────────────────────────
TP1_R_MULT = 1.5           # capped at min(1R, entry->TP3 * 1/3) inside golden_trio.py
TP2_R_MULT = 3.0           # reference constant; actual TP2 = midpoint entry->Turtle band
TP3_R_MULT = 4.0           # reference constant; actual TP3 = opposite Turtle band

PENDING_ORDER_MAX_MINUTES = 90       # 6 x M15 bars unfilled -> cancel (EXPIRED)

HARD_FLAT_UTC_HOUR = 23
HARD_FLAT_UTC_MINUTE = 59
WARNING_UTC_HOUR = 18
WARNING_UTC_MINUTE = 0

# session_cutoff=False -> 24/7 alerts, no forced hard-flat close
INSTRUMENT_PROFILES = {
    "XAUUSD": {"session_cutoff": False},
}

# ─────────────────────────────────────────────────────────────────────
# 3.  WATCH tracker timing
# ─────────────────────────────────────────────────────────────────────
WATCH_EXPIRY_HOURS = 4
WATCH_UPDATE_INTERVAL_MINUTES = 45
WATCH_UPGRADE_SCORE = APLUS_MIN_SCORE
WATCH_COLLAPSE_SCORE = 45

# ─────────────────────────────────────────────────────────────────────
# 4.  Health check
# ─────────────────────────────────────────────────────────────────────
HEALTH_CHECK_INTERVAL_HOURS = 6

# ─────────────────────────────────────────────────────────────────────
# 5.  News / economic blackout (gold reacts hard to CPI, FOMC, NFP)
# ─────────────────────────────────────────────────────────────────────
NEWS_BLACKOUT_MINUTES_AFTER = 30
ECON_CALENDAR_MIN_IMPACT = "High"
ECON_BLACKOUT_MINUTES_BEFORE = 15
ECON_BLACKOUT_MINUTES_AFTER = 15
ECON_CALENDAR_RELEVANT_CURRENCIES = {"USD"}   # gold correlates dominantly with the USD leg

# ─────────────────────────────────────────────────────────────────────
# 5.7  ATR sweet spot (still applies -- dead / too-volatile filter)
# ─────────────────────────────────────────────────────────────────────
ATR_LOOKBACK_BARS = 100
ATR_LOW_PERCENTILE = 10
ATR_HIGH_PERCENTILE = 80
ATR_DEAD_MARKET_PENALTY = -10
ATR_TOO_VOLATILE_PENALTY = -10

# ─────────────────────────────────────────────────────────────────────
# 6.  Entry expiry (pending-order age cap; ActiveEntryTracker)
# ─────────────────────────────────────────────────────────────────────
ENTRY_EXPIRY_HOURS = 2

# ─────────────────────────────────────────────────────────────────────
# 8.  Golden Trio strategy (Turtle Trade Channel + RSI + Zero Lag SMA)
# ─────────────────────────────────────────────────────────────────────
GT_RSI_PERIOD = 14
# Sequenced RSI confirmation (hard gate). For BUY:
#   1. RSI <= GT_RSI_DIP_LEVEL at some bar in the last GT_RSI_DIP_LOOKBACK
#   2. Then RSI rose for GT_RSI_RISE_BARS consecutive bars after that dip
#   3. Trigger bar closes above GT_RSI_CONFIRM_LEVEL (from below)
#   4. Trigger bar body is bullish (close > open) -- confirms the reversal
# SELL mirrors: RSI >= 100-GT_RSI_DIP_LEVEL, then fell for GT_RSI_RISE_BARS,
# closes below 100-GT_RSI_CONFIRM_LEVEL, bearish body.
# The old absolute-cross gate (dip <= 48, then cross > 50) never fired on
# trend-continuation days when RSI stayed above 50 the whole session --
# 10 straight days of zero alerts made this obvious. Replaced with
# hook-up detection: any real RSI reversal from a local low fires,
# regardless of absolute level. See _rsi_reversal_sequence in golden_trio.py.
GT_RSI_DIP_LOOKBACK = 8       # window (in bars) to search for the local low
GT_RSI_MIN_HOOK = 3           # was 5; 5 pts of RSI climb is a lot on M15
                              # gold outside London/NY -- Asian and early-Euro
                              # sessions rarely hook that hard. 3 pts still
                              # requires a real momentum shift (not noise)
                              # while catching setups the bot was missing
                              # through the quieter sessions.
GT_RSI_BUY_FLOOR = 40         # don't buy while RSI < 40 (still oversold /
                              # in a real downtrend); mirrored for SELL as
                              # (100 - GT_RSI_BUY_FLOOR).
# Kept for backwards compatibility with any imports elsewhere; unused now.
GT_RSI_DIP_LEVEL = 48
GT_RSI_RISE_BARS = 1
GT_RSI_CONFIRM_LEVEL = 50
GT_ZLSMA_PERIOD = 30           # spec calls for 50, but Capital.com's demo API
                               # caps XAUUSD 15min at ~80 bars per request so
                               # SMA-of-SMA(50) (needs ~100 bars) is unreachable.
                               # 30 stabilises within ~60 bars; the rest of the
                               # setup (RSI + Turtle) stays at spec.
GT_ZLSMA_SLOPE_LOOKBACK = 15   # wide enough to see the trend context, small
                               # enough to fit inside ZLSMA(30)'s valid window
# SMC (Smart Money Concepts) detector -- runs alongside Golden Trio.
# Uses the `smartmoneyconcepts` library for Order Block / CHOCH / Liquidity
# Sweep detection. Either detector can fire an alert; the one with higher
# quality wins if both fire on the same scan.
# SMC detectors' internal quality budget (each of OB / CHOCH / SWEEP scores
# up to this ceiling). find_candidate uses 38 as the SMC quality denominator
# when normalizing against GT's 50-pt (rsi+turtle) budget, so keep it 38.
PATTERN_QUALITY_BASE_MAX = 38

SMC_SWING_LENGTH = 3              # was 5; on M15 gold, 5 bars (~2.5h each side)
                                  # filtered out most mid-range swings so SMC
                                  # was silent for hours. 3 (~45min each side)
                                  # still requires a real pivot but surfaces
                                  # more Order Blocks / Liquidity Pools.
SMC_OB_MAX_DISTANCE_ATR = 3.0     # skip OBs further than this many ATR from current price
SMC_CHOCH_MAX_RECENCY = 5         # CHOCH must have occurred within N bars to still count
SMC_LIQUIDITY_RANGE_PCT = 0.5     # cluster-size tolerance for liquidity pools (percent)
SMC_SWEEP_RECENCY = 3             # sweep must have happened within N bars

GT_TURTLE_PERIOD = 10        # was 20; a 20-bar Donchian sits so far from
                              # price during trends that the proximity gate
                              # never satisfies. 10 bars tracks price closer
                              # so real pullbacks actually touch the band.
# Band-proximity in ATR. Progressive loosening: 0.5 (too tight, silent),
# 1.0 (still silent on trend-continuation days -- pullbacks in strong
# trends rarely reach the 20-bar Donchian extreme). 2.0 catches typical
# gold pullback entries (~1-2 ATR from the local low/high) without
# opening up to noise. Turtle is the location gate; keeping it loose
# enough to fire during real sessions is more valuable than perfect
# mean-reversion location.
GT_PROXIMITY_ATR_MULT = 2.0
# Beyond this many ATR from the Turtle band the setup is at the wrong
# end of the range -- would take a huge trend-continuation move to hit
# TP. Hard-vetoed. Anything between GT_PROXIMITY_ATR_MULT and this cap
# now fires with a smoothly decaying quality score instead of being
# silently rejected.
GT_PROXIMITY_ATR_HARD_VETO = 5.0
GT_SL_BUFFER_ATR_MULT = 0.25
GT_QUALITY_MAX = 40

# ZLSMA direction thresholds (fraction of ATR change over slope lookback):
#   |slope| <  GT_ZLSMA_FLAT_ATR_FRAC  -> flat  (WATCH ok, A+ blocked)
#   slope aligns with direction        -> aligned (full points)
#   slope opposes direction            -> against (no signal at all)
GT_ZLSMA_FLAT_ATR_FRAC = 0.15

# Chop/range-compression filter. If the last GT_CHOP_LOOKBACK bars' range
# fits inside GT_CHOP_MIN_RANGE_ATR * ATR, the market is chopping and no
# signal fires (avoids the RSI-flip-flop BUY/SELL churn).
# Trigger-bar body veto. Only reject a candidate if the trigger bar closed
# *decisively* in the opposite direction; a doji or small counter-body still
# passes, since rsi-seq + turtle + zlsma already confirm the reversal.
# 0.5 = counter body must be > 50% of the candle's total range to block.
GT_COUNTER_BODY_MAX_RATIO = 0.5

GT_CHOP_LOOKBACK = 20
GT_CHOP_MIN_RANGE_ATR = 2.0  # was 3.0; 3x ATR range over 5 hours (20 M15 bars)
                              # is stricter than gold typically hits outside
                              # strong-trend days. 2x still filters truly flat
                              # markets but allows normal-session movement.

# Cooldown between alerts to prevent duplicate/flip-flop spam.
# Opposite-direction cooldown is now overridden by any SMC structural
# event (Order Block / CHOCH / Liquidity Sweep) -- a fresh sweep is the
# "genuine new structure" that justifies reversing the previous alert.
COOLDOWN_SAME_DIRECTION_MINUTES = 30
COOLDOWN_SAME_DIRECTION_POINTS = 30    # override cooldown if price moved this far
COOLDOWN_OPPOSITE_DIRECTION_MINUTES = 30  # was 60; reduced per user directive #7

# Existing WATCH supersede threshold. A new WATCH candidate replaces the
# active one when: different direction, score >= active + this margin, or
# a fresh SMC structural event. Prevents a stale mediocre WATCH from
# masking a much stronger later setup.
WATCH_SUPERSEDE_SCORE_MARGIN = 10

# ─────────────────────────────────────────────────────────────────────
# 9.1  Core principles
# ─────────────────────────────────────────────────────────────────────
ALERT_ONLY = True  # never executes trades
