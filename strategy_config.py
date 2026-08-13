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
# 1.3  Round-number levels (gold trades in 50-dollar increments; 3 pts proximity)
# ─────────────────────────────────────────────────────────────────────
ROUND_NUMBER_BONUS = 5
ROUND_NUMBER_OFFSET_TABLE = {
    "XAUUSD": (50, 3),
}

# ─────────────────────────────────────────────────────────────────────
# 1.4  Alert thresholds (unchanged score bands so tracker/WATCH logic keeps working)
# ─────────────────────────────────────────────────────────────────────
NO_ALERT_MAX = 61          # score < 62 -> no alert
WATCH_MIN_SCORE = 62
WATCH_MAX_SCORE = 74
APLUS_MIN_SCORE = 75

DAILY_LOSS_LIMIT_USD = 20.0
DAILY_LOSS_BREAKER_DURATION_DAYS = 14

# ─────────────────────────────────────────────────────────────────────
# 1.5  Trade lifecycle (used by ActiveEntryTracker / OpenTradeTracker)
# ─────────────────────────────────────────────────────────────────────
TP1_R_MULT = 1.5           # capped at min(1R, entry->TP3 * 1/3) inside golden_trio.py
TP2_R_MULT = 3.0           # reference constant; actual TP2 = midpoint entry->Turtle band
TP3_R_MULT = 4.0           # reference constant; actual TP3 = opposite Turtle band

PENDING_ORDER_MAX_MINUTES = 90       # 6 x M15 bars unfilled -> cancel (EXPIRED)

HARD_FLAT_UTC_HOUR = 18
HARD_FLAT_UTC_MINUTE = 30
WARNING_UTC_HOUR = 18
WARNING_UTC_MINUTE = 0

INSTRUMENT_PROFILES = {
    "XAUUSD": {"session_cutoff": True},
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
GT_RSI_OVERSOLD = 20
GT_RSI_OVERBOUGHT = 80
GT_RSI_CONFIRM_LEVEL = 40   # cross-up trigger for BUY; 60 (=100-40) for SELL
GT_RSI_OVERSOLD_LOOKBACK = 3
GT_ZLSMA_PERIOD = 30           # spec calls for 50, but Capital.com's demo API
                               # caps XAUUSD 15min at ~80 bars per request so
                               # SMA-of-SMA(50) (needs ~100 bars) is unreachable.
                               # 30 stabilises within ~60 bars; the rest of the
                               # setup (RSI + Turtle) stays at spec.
GT_ZLSMA_SLOPE_LOOKBACK = 15   # wide enough to see the trend context, small
                               # enough to fit inside ZLSMA(30)'s valid window
GT_TURTLE_PERIOD = 20
GT_PROXIMITY_ATR_MULT = 0.75
GT_SL_BUFFER_ATR_MULT = 0.25
GT_QUALITY_MAX = 40

# ─────────────────────────────────────────────────────────────────────
# 9.1  Core principles
# ─────────────────────────────────────────────────────────────────────
ALERT_ONLY = True  # never executes trades
