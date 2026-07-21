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
    "EURUSD": {"name": "Euro/Dollar", "search": "EUR/USD",      "class": "FOREX"},
    "GBPJPY": {"name": "GBP/JPY",   "search": "GBP/JPY",        "class": "FOREX_JPY"},
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

VWAP_FILTER_MATCH = 4     # price on the correct side of anchored (session) VWAP
VWAP_FILTER_NEUTRAL = 0
VWAP_FILTER_AGAINST = -3

ROUND_NUMBER_BONUS = 5
VOLUME_CONFIRM_BONUS = 3  # now: sweep level sits inside the volume-profile value area

HIGH_ATR_PENALTY = -10
CHOPPY_MARKET_PENALTY = -10

NEWS_SPIKE_ATR_MULT = 2.5   # a candle range >= this many ATRs is treated as a news-like spike
RECENT_SPIKE_LOOKBACK = 3   # bars checked (excluding current) for a recent spike
RECENT_SPIKE_PENALTY = -8   # applied to non-NEWS_RETEST patterns firing right after a spike

# Reactive (not predictive) news blackout via a public RSS headline feed --
# no scheduled-event time is known ahead of time, so this only pauses new
# alerts *after* a matching headline is published, not before.
NEWS_BLACKOUT_MINUTES_AFTER = 30

# Real (predictive) economic-calendar blackout -- a forward-looking
# complement to the reactive RSS feed above. Only fires for a scheduled
# High-impact release in a currency at least one tracked instrument cares
# about (USD indices + BTCUSD, EUR/USD for EURUSD, GBP/JPY for GBPJPY).
ECON_CALENDAR_MIN_IMPACT = "High"
ECON_BLACKOUT_MINUTES_BEFORE = 15
ECON_BLACKOUT_MINUTES_AFTER = 15
ECON_CALENDAR_RELEVANT_CURRENCIES = {"USD", "EUR", "GBP", "JPY"}

# Whale-flow confirmation bonus (BTCUSD only -- the only on-chain instrument
# tracked). Tracks a user-supplied list of BTC addresses (see
# strategy/whale_tracker.py -- WHALE_MONITORED_ADDRESSES) and computes net
# inflow/outflow to them: deposits = distribution (bearish), withdrawals =
# accumulation (bullish). A confirmation bonus only -- never a hard block
# or penalty, consistent with every other scoring bonus in
# scoring_strategy.py ("detect generously, score strictly").
WHALE_FLOW_SIGNIFICANT_USD = 3_000_000  # netflow must clear this to count as a real signal
WHALE_FLOW_BONUS = 8

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
# 1.5  Entry & exit logic (Entry/SL/TP Selection Rules v1.3 — BOS-based leg
# discovery, 50% retrace entry, structural stop w/ round-number anti-hunt
# offset, 3-tier liquidity-aware take-profit)
# ─────────────────────────────────────────────────────────────────────
BOS_SEARCH_LOOKBACK_BARS = 60        # how far back (in entry-timeframe bars) to search for the most recent BOS

ENTRY_RETRACE_PCT = 0.50             # limit entry at 50% of the leg
ENTRY_FVG_ZONE_MIN_PCT = 0.40        # FVG-midpoint entry override zone (fraction retraced from leg_end)
ENTRY_FVG_ZONE_MAX_PCT = 0.62

SL_BUFFER_ATR_MULT = 0.5             # buffer = max(SL_BUFFER_ATR_MULT x ATR, SL_BUFFER_SPREAD_MULT x spread)
SL_BUFFER_SPREAD_MULT = 2.0
ROUND_NUMBER_OFFSET_ATR_MULT = 0.15  # extra push beyond a round-number collision
# Per-instrument (round_multiple, proximity_threshold) for the SL anti-stop-hunt check.
ROUND_NUMBER_OFFSET_TABLE = {
    "US500":  (50, 3),
    "US30":   (50, 5),
    "US100":  (100, 5),
    "BTCUSD": (500, 30),
    "EURUSD": (0.0050, 0.0003),   # 50-pip levels, 3-pip proximity
    "GBPJPY": (0.500, 0.100),     # 50-pip levels (JPY pip=0.01), 10-pip proximity
}

TP1_R_MULT = 1.0
TP1_EXCEPTION_MIN_R = 0.8            # an unfilled FVG/minor swing in [0.8R, 1.0R) overrides raw TP1
TP1_EXCEPTION_MAX_R = 1.0
TP2_R_MULT = 1.8                     # fallback when no liquidity level sits beyond TP1
TP3_R_MULT = 2.8                     # fallback (also the ceiling vs. any external level beyond TP2)

PENDING_ORDER_MAX_MINUTES = 90       # 6 x M15 bars unfilled -> cancel (EXPIRED)

HARD_FLAT_UTC_HOUR = 18
HARD_FLAT_UTC_MINUTE = 30           # no new entry alerts after 18:30 UTC (instruments with session_cutoff on)
WARNING_UTC_HOUR = 18
WARNING_UTC_MINUTE = 0              # heads-up alert to manually close before the 18:30 hard flat
BTC_EXEMPT_FROM_US_INDEX_DEDUP = True

# session_cutoff: whether HARD_FLAT_UTC_HOUR/MINUTE applies to this instrument.
# All six now included -- v1.3 explicitly applies the same session discipline
# to BTCUSD too ("no session structure" for liquidity levels doesn't mean no
# session discipline for exiting).
INSTRUMENT_PROFILES = {
    "US100":  {"session_cutoff": True},
    "US500":  {"session_cutoff": True},
    "US30":   {"session_cutoff": True},
    "BTCUSD": {"session_cutoff": True},
    "EURUSD": {"session_cutoff": True},
    "GBPJPY": {"session_cutoff": True},
}
# Removed and staying removed: XAUUSD.

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
IFVG_BONUS = 8   # a violated FVG that flips polarity; same weight as an untested FVG (no data to justify weighting it higher)

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
