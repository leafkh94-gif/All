"""
Trading Alert Bot — configuration.
Instruments, scoring thresholds, timing rules. See Bot_Spec_V3 Section 1.
"""

# ─────────────────────────────────────────────────────────────────────
# 1.1  Instruments  (Gold/XAUUSD was removed — do NOT re-add it)
# ─────────────────────────────────────────────────────────────────────
INSTRUMENTS = {
    "US500": {"name": "S&P 500",   "search": "S&P 500",       "class": "US_INDEX"},
    "US100": {"name": "Nasdaq 100", "search": "NASDAQ 100",    "class": "US_INDEX"},
    "US30":  {"name": "Dow Jones",  "search": "Wall Street 30", "class": "US_INDEX"},
    "BTCUSD": {"name": "Bitcoin",   "search": "bitcoin",        "class": "CRYPTO"},
}

US_INDEX_INSTRUMENTS = [k for k, v in INSTRUMENTS.items() if v["class"] == "US_INDEX"]
CRYPTO_INSTRUMENTS = [k for k, v in INSTRUMENTS.items() if v["class"] == "CRYPTO"]

# ─────────────────────────────────────────────────────────────────────
# 1.2  Architecture
# ─────────────────────────────────────────────────────────────────────
SCAN_INTERVAL_MINUTES = 15

# ─────────────────────────────────────────────────────────────────────
# 1.3  Scoring system — max points per factor (kept as-is)
# ─────────────────────────────────────────────────────────────────────
PATTERN_QUALITY_BASE_MAX = 38
PATTERN_QUALITY_BONUS_MAX = 10

TECHNICAL_CONFIRM_ALL_ALIGNED = 10   # 2-3 of RSI/MACD/EMA aligned
TECHNICAL_CONFIRM_ONE_ALIGNED = 4
TECHNICAL_CONFIRM_NONE_ALIGNED = 0

DAILY_BIAS_WITH_TREND = 15
DAILY_BIAS_NEUTRAL = 5
DAILY_BIAS_COUNTER_TREND = -8

MA20_FILTER_MATCH = 4
MA20_FILTER_NEUTRAL = 0
MA20_FILTER_AGAINST = -3

ROUND_NUMBER_BONUS = 5
VOLUME_CONFIRM_BONUS = 3

HIGH_ATR_PENALTY = -10
CHOPPY_MARKET_PENALTY = -10

NEWS_SPIKE_ATR_MULT = 2.5   # a candle range >= this many ATRs is treated as a news-like spike
RECENT_SPIKE_LOOKBACK = 3   # bars checked (excluding current) for a recent spike
RECENT_SPIKE_PENALTY = -8   # applied to non-NEWS_RETEST patterns firing right after a spike

NEWS_BLACKOUT_MINUTES_BEFORE = 15   # pause new alerts this long before a high-impact event
NEWS_BLACKOUT_MINUTES_AFTER = 15    # ...and this long after it
NEWS_CALENDAR_MIN_IMPACT = "High"   # only events at/above this impact level trigger a blackout

# ─────────────────────────────────────────────────────────────────────
# 1.4  Alert thresholds
# ─────────────────────────────────────────────────────────────────────
NO_ALERT_MAX = 61          # score < 62 -> no alert
WATCH_MIN_SCORE = 62
WATCH_MAX_SCORE = 74
APLUS_MIN_SCORE = 75

DAILY_LOSS_LIMIT_USD = 20.0   # self-reported via /loss; new WATCH/A+ alerts pause once hit
DAILY_LOSS_BREAKER_DURATION_DAYS = 14   # trial window; breaker stops enforcing after this

# ─────────────────────────────────────────────────────────────────────
# 1.5  Entry & exit logic
# ─────────────────────────────────────────────────────────────────────
RETRACE_FRACTION = 0.5              # limit entry at 50% retrace of breakout candle
RETRACE_DEEPEN_ATR_MULT = 1.5       # if 50% retrace > 1.5x ATR from level -> deepen to S/R retest
MIN_RR_RATIO = 2.0                  # TP1/TP2 minimum R:R = 1:2
HARD_FLAT_UTC_HOUR = 18
HARD_FLAT_UTC_MINUTE = 30           # no new entry alerts after 18:30 UTC (US indices)
BTC_EXEMPT_FROM_US_INDEX_DEDUP = True

# ─────────────────────────────────────────────────────────────────────
# 3.  WATCH tracker timing
# ─────────────────────────────────────────────────────────────────────
WATCH_EXPIRY_HOURS = 4
WATCH_UPDATE_INTERVAL_MINUTES = 45
WATCH_UPGRADE_SCORE = APLUS_MIN_SCORE   # score >= 75 -> upgrade to A+
WATCH_COLLAPSE_SCORE = 55               # score < 55 -> pattern collapsed, cancel

# ─────────────────────────────────────────────────────────────────────
# 4.  Health check
# ─────────────────────────────────────────────────────────────────────
HEALTH_CHECK_INTERVAL_HOURS = 6

# ─────────────────────────────────────────────────────────────────────
# 5.1  PDH/PDL
# ─────────────────────────────────────────────────────────────────────
PDH_PDL_PROXIMITY_PCT = 0.001    # within 0.1%
PDH_PDL_BONUS = 10

# ─────────────────────────────────────────────────────────────────────
# 5.3  FVG
# ─────────────────────────────────────────────────────────────────────
FVG_LOOKBACK_CANDLES = 10
FVG_BONUS = 8

# ─────────────────────────────────────────────────────────────────────
# 5.4  EQH/EQL
# ─────────────────────────────────────────────────────────────────────
EQH_EQL_LOOKBACK_CANDLES = 50
EQH_EQL_TOLERANCE_PCT = 0.0005   # within 0.05%
EQH_EQL_BONUS = 10

# ─────────────────────────────────────────────────────────────────────
# 5.5  Monday weekly sweep
# ─────────────────────────────────────────────────────────────────────
WEEKLY_LEVEL_RECORD_UTC_HOUR = 21   # Friday 21:00 UTC
MONDAY_SWEEP_BONUS = 12
MONDAY_SWEEP_WINDOW_END_UTC_HOUR = 18   # Monday 00:00-18:00 UTC

# ─────────────────────────────────────────────────────────────────────
# 5.6  3-candle confirmation
# ─────────────────────────────────────────────────────────────────────
CONFIRMATION_CANDLE_BONUS = 8

# ─────────────────────────────────────────────────────────────────────
# 5.7  ATR sweet spot
# ─────────────────────────────────────────────────────────────────────
ATR_LOOKBACK_BARS = 100
ATR_LOW_PERCENTILE = 10     # < 10th percentile -> dead market
ATR_HIGH_PERCENTILE = 80    # > 80th percentile -> too volatile (lowered from 90th)
ATR_DEAD_MARKET_PENALTY = -10
ATR_TOO_VOLATILE_PENALTY = -10

# ─────────────────────────────────────────────────────────────────────
# 6.  Entry expiry
# ─────────────────────────────────────────────────────────────────────
ENTRY_EXPIRY_HOURS = 2

# ─────────────────────────────────────────────────────────────────────
# 9.1  Core principles
# ─────────────────────────────────────────────────────────────────────
ALERT_ONLY = True  # never executes trades
