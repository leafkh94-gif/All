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
    # Added for the AUD/JPY-USD risk-on/off correlation cluster + Asia-Pacific
    # index coverage. Search terms below are best-guess, matching the exact
    # naming convention already used for EURUSD/GBPJPY -- NOT verified
    # against a live Capital.com account from this sandbox (no network
    # access here). Confirm each resolves via /scan once merged; if a
    # search term returns no/the wrong market, fix the string here, no
    # other code needs to change (CapitalFeed.resolve_epics() does a live
    # /markets search per instrument, nothing is hardcoded beyond this).
    "AUDJPY": {"name": "AUD/JPY",   "search": "AUD/JPY",        "class": "FOREX_JPY"},
    "AUDUSD": {"name": "AUD/USD",   "search": "AUD/USD",        "class": "FOREX"},
    "USDJPY": {"name": "USD/JPY",   "search": "USD/JPY",        "class": "FOREX_JPY"},
    "JP225":  {"name": "Nikkei 225", "search": "Japan 225",     "class": "ASIA_INDEX"},
    "HK50":   {"name": "Hang Seng", "search": "Hong Kong 50",   "class": "ASIA_INDEX"},
    "A50":    {"name": "China A50", "search": "China A50",      "class": "ASIA_INDEX"},
}

# AUDJPY/AUDUSD/USDJPY/JP225 move together as one risk-on/off cluster (AUD
# and JPY-JPY-crosses + the correlated Japanese equity index) -- flagged in
# alert text only (see main_alerts.py's format_watch_alert/format_aplus_alert),
# never deduped/suppressed like US_INDEX_INSTRUMENTS below. A trader seeing
# two of these fire in the same cycle should read it as one macro move, not
# two independent confirmations.
CORRELATION_CLUSTER = {"AUDJPY", "AUDUSD", "USDJPY", "JP225"}
CORRELATION_CLUSTER_WARNING = (
    "⚠️ Correlated cluster (AUD/JPY-USD risk-on/off + JP225) — treat as ONE move, "
    "not an independent signal. Check the other instruments in this cluster first."
)

US_INDEX_INSTRUMENTS = [k for k, v in INSTRUMENTS.items() if v["class"] == "US_INDEX"]
CRYPTO_INSTRUMENTS = [k for k, v in INSTRUMENTS.items() if v["class"] == "CRYPTO"]

# ─────────────────────────────────────────────────────────────────────
# Strategy v3.2 scope — the bot scans ONLY these four instruments (S&P 500,
# Nasdaq 100, Dow Jones, Bitcoin) on H1. The other entries in INSTRUMENTS
# above are kept for config/lookups and can be re-activated by adding them
# back here, but v3.2 is deliberately a focused 4-instrument strategy.
# ─────────────────────────────────────────────────────────────────────
ACTIVE_INSTRUMENTS = ["US500", "US100", "US30", "BTCUSD"]

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
# strategy/whale_tracker.py -- WHALE_MONITORED_ADDRESSES) via Blockstream's
# free Esplora API and computes net inflow/outflow to them: deposits =
# distribution (bearish), withdrawals = accumulation (bullish). Amounts are
# native BTC (Esplora has no USD conversion). A confirmation bonus only --
# never a hard block or penalty, consistent with every other scoring bonus
# in scoring_strategy.py ("detect generously, score strictly").
WHALE_FLOW_LOOKBACK_MINUTES = 60      # only transactions confirmed within this window count
WHALE_FLOW_SIGNIFICANT_BTC = 50.0     # netflow must clear this (in BTC) to count as a real signal
WHALE_FLOW_BONUS = 8

# ─────────────────────────────────────────────────────────────────────
# 1.4  Alert thresholds (Strategy v3.2 — WATCH >= 72, A+ >= 82)
# ─────────────────────────────────────────────────────────────────────
NO_ALERT_MAX = 71          # score < 72 -> no alert
WATCH_MIN_SCORE = 72
WATCH_MAX_SCORE = 81
APLUS_MIN_SCORE = 82

# Adaptive A+ threshold (v3.2 §2.1): starts at APLUS_BASE_SCORE, drops
# APLUS_QUIET_STEP points for each day of silence beyond APLUS_QUIET_DAYS
# (floored at APLUS_MIN_FLOOR), and rises APLUS_BUSY_STEP per extra signal
# on busy days (capped at APLUS_MAX_CAP). Goal: never go quiet for weeks in
# a calm market, never flood in an active one. WATCH stays fixed at 72.
APLUS_BASE_SCORE = 82
APLUS_MIN_FLOOR = 68
APLUS_MAX_CAP = 90
APLUS_QUIET_DAYS = 3          # days with no signal before the threshold starts easing
APLUS_QUIET_STEP = 2         # points shaved per quiet day past APLUS_QUIET_DAYS
APLUS_BUSY_SIGNALS = 3       # today's alert count at/above this counts as a "busy" day
APLUS_BUSY_STEP = 1          # points added per signal past APLUS_BUSY_SIGNALS

# ─────────────────────────────────────────────────────────────────────
# 1.4b  Daily alert budget / spacing (v3.2 §2.2)
# ─────────────────────────────────────────────────────────────────────
MAX_APLUS_PER_DAY = 5
MAX_WATCH_PER_DAY = 10
INSTRUMENT_COOLDOWN_HOURS = 4      # per-instrument quiet window after any alert on it
MIN_MINUTES_BETWEEN_ALERTS = 120   # >= 2h between ANY two alerts, whatever the instrument

DAILY_LOSS_LIMIT_USD = 20.0   # self-reported via /loss; new WATCH/A+ alerts pause once hit
DAILY_LOSS_BREAKER_DURATION_DAYS = 14   # trial window; breaker stops enforcing after this

# Fixed news-blackout windows (v3.2 §8) — the two recurring UTC windows when
# the highest-impact US macro releases (08:30 ET data, 14:00 UTC events)
# cluster. Deliberately time-fixed, not tied to a live calendar (that
# limitation is acknowledged in the strategy doc). Complements the reactive
# RSS feed and the predictive economic_calendar module, which stay active.
NEWS_FIXED_BLACKOUT_WINDOWS = [
    ((12, 25), (13, 5)),
    ((13, 25), (14, 5)),
]

# ─────────────────────────────────────────────────────────────────────
# 1.5  Entry & exit logic (Entry/SL/TP Selection Rules v1.3 — BOS-based leg
# discovery, 50% retrace entry, structural stop w/ round-number anti-hunt
# offset, 3-tier liquidity-aware take-profit)
# ─────────────────────────────────────────────────────────────────────
BOS_SEARCH_LOOKBACK_BARS = 60        # how far back (in entry-timeframe bars) to search for the most recent BOS

ENTRY_RETRACE_PCT = 0.50             # limit entry at 50% of the leg
ENTRY_FVG_ZONE_MIN_PCT = 0.40        # FVG-midpoint entry override zone (fraction retraced from leg_end)
ENTRY_FVG_ZONE_MAX_PCT = 0.62

SL_BUFFER_ATR_MULT = 1.0             # v3.2 §7.1: buffer = max(1.0 x ATR, 3 x spread) behind the structural anchor
SL_BUFFER_SPREAD_MULT = 3.0
# v3.2 §7.1 step 3 — clamp the final entry-to-stop distance into an allowed
# band so the stop is neither so tight that normal wobble takes it out, nor
# so wide the R:R math collapses. Bitcoin gets a slightly tighter ceiling
# (it already carries a much larger absolute ATR).
MIN_RISK_ATR_MULT = 2.0
MAX_RISK_ATR_MULT = 4.0
MAX_RISK_ATR_MULT_BTC = 3.5
ROUND_NUMBER_OFFSET_ATR_MULT = 0.15  # extra push beyond a round-number collision
# Per-instrument (round_multiple, proximity_threshold) for the SL anti-stop-hunt check.
ROUND_NUMBER_OFFSET_TABLE = {
    "US500":  (50, 3),
    "US30":   (50, 5),
    "US100":  (100, 5),
    "BTCUSD": (500, 30),
    "EURUSD": (0.0050, 0.0003),   # 50-pip levels, 3-pip proximity
    "GBPJPY": (0.500, 0.100),     # 50-pip levels (JPY pip=0.01), 10-pip proximity
    # First-pass values for the 6 new instruments -- same reasoning as
    # GBPJPY/EURUSD (JPY pairs use a 0.01 pip so 0.50/0.10 = 50/10 pips;
    # non-JPY FX mirrors EURUSD's 0.0050/0.0003), tune once live data shows
    # where round-number stop hunts actually cluster for each.
    "AUDJPY": (0.500, 0.100),
    "USDJPY": (0.500, 0.100),
    "AUDUSD": (0.0050, 0.0003),
    "JP225":  (100, 10),
    "HK50":   (100, 10),
    "A50":    (50, 5),
}

# v3.2 §7.2 — two fixed R-multiple targets. TP1 = 2 x risk (1:2), TP2 = 3 x
# risk (1:3). No pooled-liquidity override and no third runner tier: the doc
# defines exactly two targets, close half at TP1 (SL -> breakeven) and the
# rest at TP2.
TP1_R_MULT = 2.0
TP2_R_MULT = 3.0

PENDING_ORDER_MAX_MINUTES = 480      # v3.2 §6.3: setup validity = 8 hours on H1

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
    "AUDJPY": {"session_cutoff": True},
    "AUDUSD": {"session_cutoff": True},
    "USDJPY": {"session_cutoff": True},
    "JP225":  {"session_cutoff": True},
    "HK50":   {"session_cutoff": True},
    "A50":    {"session_cutoff": True},
}
# Removed and staying removed: XAUUSD.

# ─────────────────────────────────────────────────────────────────────
# 3.  WATCH tracker timing
# ─────────────────────────────────────────────────────────────────────
WATCH_EXPIRY_HOURS = 4
WATCH_UPDATE_INTERVAL_MINUTES = 45
WATCH_UPGRADE_SCORE = APLUS_MIN_SCORE   # score >= 82 -> upgrade to A+
WATCH_COLLAPSE_SCORE = 65               # score < 65 -> pattern collapsed, cancel (below the 72 WATCH floor)

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

# v3.2 §8 — absolute volatility guard. If ATR(14) / price exceeds this ratio
# the market is too wild for the structural stops to make sense, so the
# opportunity is cancelled outright (a hard block, not a score penalty).
# Bitcoin runs a much wider band than the indices.
ATR_RATIO_MAX = 0.018       # 1.8% for the indices
ATR_RATIO_MAX_BTC = 0.05    # 5% for Bitcoin

# ─────────────────────────────────────────────────────────────────────
# 6.  Entry expiry
# ─────────────────────────────────────────────────────────────────────
ENTRY_EXPIRY_HOURS = 8   # v3.2 §6.3 setup validity

# ─────────────────────────────────────────────────────────────────────
# 9.1  Core principles
# ─────────────────────────────────────────────────────────────────────
ALERT_ONLY = True  # never executes trades
