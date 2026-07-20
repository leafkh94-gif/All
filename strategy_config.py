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
# 1.5  Entry & exit logic (Bot Spec V4 Sections 1-3 — leg-based entry,
# structural stop, liquidity-capped TP2)
# ─────────────────────────────────────────────────────────────────────
MIN_RR_RATIO = 2.0                  # TP1/TP2 minimum R:R = 1:2
MIN_RR_AFTER_CAP = 1.5              # if TP2 liquidity-capping drops R:R below this, skip the alert
MIN_RR_TP1_AFTER_CAP = 1.0          # don't cap TP1 below this R:R -- not worth an early partial otherwise

# Trader-review fixes (post-Spec-V4): some patterns' leg_extreme sits on the
# same candle as the breakout close, giving a "leg" that's really just that
# candle's own range -- far smaller than the real move behind the setup, and
# small enough to produce entries that barely differ from market price and
# stops with almost no real breathing room. These floors/scales correct for
# that without touching pattern detection itself.
MIN_LEG_ATR_MULT = 1.0              # floor for the leg size used to scale the retrace entry
STOP_BUFFER_MIN_ATR_MULT = 0.35     # was a flat 0.25x ATR; raised, and now also leg-scaled below
STOP_BUFFER_LEG_FRACTION = 0.15     # extra buffer proportional to the (floored) leg size
MIN_FVG_SIZE_ATR_MULT = 0.15        # ignore FVG zones smaller than this fraction of ATR for entry override

HARD_FLAT_UTC_HOUR = 18
HARD_FLAT_UTC_MINUTE = 30           # no new entry alerts after 18:30 UTC (instruments with session_cutoff on)
BTC_EXEMPT_FROM_US_INDEX_DEDUP = True

# Per-instrument calibration -- these values are provisional/placeholder,
# pending the offline calibration tool (strategy/pullback_calibration.py).
# retrace_pct: fraction of the impulse leg the entry retraces back into.
# entry_expiry_mult: multiplies ActiveEntryTracker's base entry-expiry window.
# session_cutoff: whether HARD_FLAT_UTC_HOUR/MINUTE applies to this instrument.
INSTRUMENT_PROFILES = {
    "US100":  {"retrace_pct": 0.40, "entry_expiry_mult": 0.75, "session_cutoff": True},
    "US500":  {"retrace_pct": 0.50, "entry_expiry_mult": 1.00, "session_cutoff": True},
    "US30":   {"retrace_pct": 0.60, "entry_expiry_mult": 1.25, "session_cutoff": True},
    "BTCUSD": {"retrace_pct": 0.50, "entry_expiry_mult": 1.50, "session_cutoff": False},
    "EURUSD": {"retrace_pct": 0.50, "entry_expiry_mult": 1.00, "session_cutoff": True},
    # "The Dragon" -- large, fast intraday ranges (BoJ intervention risk included).
    # No calibration data yet; the structural stop already scales with this
    # instrument's own ATR, so it isn't defenseless against the bigger wicks,
    # but treat these numbers as even less settled than the others until
    # pullback_calibration.py has actually run against it.
    "GBPJPY": {"retrace_pct": 0.50, "entry_expiry_mult": 0.75, "session_cutoff": True},
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
