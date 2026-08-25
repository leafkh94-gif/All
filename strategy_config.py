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
# 1.2b  Small-scalp fixed-target mode
# ─────────────────────────────────────────────────────────────────────
# TARGET_MODE = "FIXED" bypasses golden_trio's structural stop/TP logic and
# uses fixed point offsets instead. TARGET_MODE = "STRUCTURAL" (or anything
# else) keeps the original Turtle-band-derived targets.
#
# Cost-aware calibration for gold on M15:
#   POINT_VALUE = 1.0 means 1 pt = $1 of price. Simpler mental model than
#   the previous 0.1 mapping.
#   SL = $25 -- roughly 3-8x M15 gold ATR, wide enough that random wiggle
#   inside a real setup doesn't hit the stop.
#   TP1 = $25 (1R), TP2 = $50 (2R), TP3 = $100 (4R). Three genuinely
#   distinct tiers; TP3 is no longer collapsed onto TP2.
#   MAX_SPREAD $1.50 = 6% of the $25 stop, versus the old 25% ratio that
#   ate any edge before the trade even played out.
TARGET_MODE = "FIXED"
POINT_VALUE = 1.0
FIXED_SL_POINTS = 25
FIXED_TP1_POINTS = 25
FIXED_TP2_POINTS = 50
FIXED_TP3_POINTS = 100
MAX_SPREAD_POINTS = 1.5

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
# Killzone bonus removed (SCORE_KILLZONE_MAX=0 below) — it concentrated
# alerts into 13:00–16:00 UTC by adding 10 pts during the London/NY
# overlap and 0 outside it, so Asian/early-European setups needed 10
# extra "real" points to reach WATCH. With that gone, WATCH threshold
# dropped 55 → 45 so a real setup (RSI hook + Turtle proximity +
# ZLSMA aligned ≈ 50 pts even before H4) qualifies in every session.
NO_ALERT_MAX = 44
WATCH_MIN_SCORE = 45
WATCH_MAX_SCORE = 69
APLUS_MIN_SCORE = 70

# Score budget (rewritten from base-60 model). Max ≈ 100.
SCORE_RSI_CONFIRM_MAX = 30      # sequenced RSI reversal quality
SCORE_TURTLE_MAX = 20           # proximity to Turtle band (0.5*ATR tight)
SCORE_ZLSMA_ALIGNED = 20        # ZLSMA slope aligned with entry direction
SCORE_ZLSMA_FLAT = 0            # flat slope contributes nothing (blocks A+)
SCORE_H4_ALIGNED = 15
SCORE_H4_FLAT = 0
SCORE_H4_OPPOSED = -10          # was -15; combined with WATCH_MIN=45, -15
                                # meant every H4-opposed setup fell just
                                # under WATCH (typical opposed score 39-42)
                                # so the bot went silent whenever the H4
                                # trend was persistent. -10 still tilts
                                # the score against opposition (backtest
                                # confirmed opposed signals lose -0.09R
                                # avg vs aligned +0.05R) without turning
                                # H4 into a hard veto. A+ still blocked
                                # for opposed via score_candidate's
                                # aplus_eligible gate.
SCORE_KILLZONE_MAX = 0          # was 10; the bonus concentrated alerts
                                # into the London/NY overlap and left the
                                # rest of the day artificially short of
                                # threshold. Gold trades 24/5 -- alerts
                                # should reflect actual setup quality,
                                # not what time it is.
SCORE_ROUND_NUMBER = 5
SCORE_ATR_SWEET_SPOT_PENALTY = -10

# Formerly hard vetoes in golden_trio.py, now soft score penalties so a
# strong M15 setup can still qualify as WATCH even when the trend
# indicator or the volatility regime is unfavorable. A+ is still blocked
# in both cases (see score_candidate.aplus_eligible).
SCORE_ZLSMA_AGAINST = -12       # ZLSMA slope opposes the entry direction
SCORE_CHOP_PENALTY = -10        # recent range is compressed (chop regime)

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
