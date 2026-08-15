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
SCAN_INTERVAL_MINUTES = 5

# ─────────────────────────────────────────────────────────────────────
# 1.2b  Small-scalp fixed-target mode
# ─────────────────────────────────────────────────────────────────────
# TARGET_MODE = "FIXED" bypasses golden_trio's structural stop/TP logic and
# uses fixed point offsets instead. TARGET_MODE = "STRUCTURAL" (or anything
# else) keeps the original Turtle-band-derived targets.
TARGET_MODE = "FIXED"
POINT_VALUE = 0.1        # price distance per "point" (0.1 -> 20pt=2usd; 1.0 -> 20usd)
FIXED_SL_POINTS = 20
FIXED_TP1_POINTS = 20
FIXED_TP2_POINTS = 40
MAX_SPREAD_POINTS = 5.0  # skip signal if live spread wider than this many points
                          # (at POINT_VALUE=0.1 -> $0.50 spread ceiling for gold)

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
NO_ALERT_MAX = 54
WATCH_MIN_SCORE = 55
WATCH_MAX_SCORE = 74
APLUS_MIN_SCORE = 75

# Score budget (rewritten from base-60 model). Max ≈ 100.
SCORE_RSI_CONFIRM_MAX = 30      # sequenced RSI reversal quality
SCORE_TURTLE_MAX = 20           # proximity to Turtle band (0.5*ATR tight)
SCORE_ZLSMA_ALIGNED = 20        # ZLSMA slope aligned with entry direction
SCORE_ZLSMA_FLAT = 0            # flat slope contributes nothing (blocks A+)
SCORE_H4_ALIGNED = 15
SCORE_H4_FLAT = 0
SCORE_H4_OPPOSED = -15          # opposed EMA slope; also blocks A+
SCORE_KILLZONE_MAX = 10
SCORE_ROUND_NUMBER = 5
SCORE_ATR_SWEET_SPOT_PENALTY = -10

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
WATCH_COLLAPSE_SCORE = 55

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
GT_RSI_DIP_LEVEL = 45
GT_RSI_DIP_LOOKBACK = 5
GT_RSI_RISE_BARS = 3
GT_RSI_CONFIRM_LEVEL = 50
GT_ZLSMA_PERIOD = 30           # spec calls for 50, but Capital.com's demo API
                               # caps XAUUSD 15min at ~80 bars per request so
                               # SMA-of-SMA(50) (needs ~100 bars) is unreachable.
                               # 30 stabilises within ~60 bars; the rest of the
                               # setup (RSI + Turtle) stays at spec.
GT_ZLSMA_SLOPE_LOOKBACK = 15   # wide enough to see the trend context, small
                               # enough to fit inside ZLSMA(30)'s valid window
GT_TURTLE_PERIOD = 20
# Tightened band-proximity: 0.5 ATR (was 1.5). Recent-bar extreme must touch
# the band this close for it to count as "supported by / rejected at" the band.
GT_PROXIMITY_ATR_MULT = 0.5
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
GT_CHOP_LOOKBACK = 20
GT_CHOP_MIN_RANGE_ATR = 3.0

# Cooldown between alerts to prevent duplicate/flip-flop spam.
COOLDOWN_SAME_DIRECTION_MINUTES = 30
COOLDOWN_SAME_DIRECTION_POINTS = 30    # override cooldown if price moved this far
COOLDOWN_OPPOSITE_DIRECTION_MINUTES = 60

# ─────────────────────────────────────────────────────────────────────
# 9.1  Core principles
# ─────────────────────────────────────────────────────────────────────
ALERT_ONLY = True  # never executes trades
