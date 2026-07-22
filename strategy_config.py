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
# Correlated groups that move as one macro trade. Each is (label, members).
# A single move across a cluster must NOT read as N independent confirmations,
# and (v2) must NOT open N parallel trades on the same idea -- so the alert
# carries a warning AND the "one active setup" gate is applied cluster-wide
# (see main_alerts.py). US500/US100/US30 are one move (the missing case the
# review flagged); the AUD/JPY-USD risk-on/off group is the original one.
CORRELATION_CLUSTERS = [
    ("AUD/JPY-USD risk-on/off + JP225", {"AUDJPY", "AUDUSD", "USDJPY", "JP225"}),
    ("US indices (US500/US100/US30)", {"US500", "US100", "US30"}),
]


def correlation_cluster_of(instrument):
    """(label, members) of the cluster this instrument belongs to, or None."""
    for label, members in CORRELATION_CLUSTERS:
        if instrument in members:
            return label, members
    return None

US_INDEX_INSTRUMENTS = [k for k, v in INSTRUMENTS.items() if v["class"] == "US_INDEX"]
CRYPTO_INSTRUMENTS = [k for k, v in INSTRUMENTS.items() if v["class"] == "CRYPTO"]

# ─────────────────────────────────────────────────────────────────────
# 1.2  Architecture
# ─────────────────────────────────────────────────────────────────────
SCAN_INTERVAL_MINUTES = 15

# ─────────────────────────────────────────────────────────────────────
# 1.3  Scoring system — max points per factor (kept as-is)
# ─────────────────────────────────────────────────────────────────────
# v2: base capped at 25 (was 38) so the base + H4 bias alone can no longer
# clear the WATCH gate -- forces genuine multi-axis confluence.
PATTERN_QUALITY_BASE_MAX = 25
PATTERN_QUALITY_BONUS_MAX = 10

# v2: liquidity confluences (PDH/PDL, EQH/EQL, round number) all key off the
# SAME sweep price -- previously summed to +25 for one fact. Now grouped into a
# single capped bonus: base for the first confluence type present, a small
# extra per additional distinct type, capped.
LIQUIDITY_CONFLUENCE_BASE = 8
LIQUIDITY_CONFLUENCE_EXTRA = 2
LIQUIDITY_CONFLUENCE_CAP = 12

# v2: hard gates layered on top of the score threshold. A setup must clear ALL
# THREE independent axes (structure + timing + context); and a choppy market
# blocks outright rather than a soft penalty.
INDEPENDENCE_MIN_AXES = 3
CHOPPY_GATE_THRESHOLD = 61.8   # choppiness_index above this = blocked (was a -10 nudge)

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

# v2: the displacement usually continues past the BOS candle, so freezing
# leg_end at the BOS close truncated the leg (entry too shallow, R too small).
# Extend the leg-extreme window this many bars past BOS, then freeze.
LEG_END_EXTENSION_BARS = 3

ENTRY_RETRACE_PCT = 0.50             # limit entry at 50% of the leg
ENTRY_FVG_ZONE_MIN_PCT = 0.40        # FVG-midpoint entry override zone (fraction retraced from leg_end)
ENTRY_FVG_ZONE_MAX_PCT = 0.62

# v2: thicker, further-back stop (was 0.5xATR / 2xspread -- too thin, parked
# right on the sweep wick where it gets hunted again).
SL_BUFFER_ATR_MULT = 1.0             # buffer = max(SL_BUFFER_ATR_MULT x ATR, SL_BUFFER_SPREAD_MULT x spread)
SL_BUFFER_SPREAD_MULT = 3.0
ROUND_NUMBER_OFFSET_ATR_MULT = 0.15  # extra push beyond a round-number collision
# v2: reject a setup whose risk is smaller than this x ATR -- an artificially
# tiny R (from a truncated leg or a too-close stop) is not worth taking.
MIN_RISK_ATR_MULT = 0.8
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

TP1_R_MULT = 1.0
TP1_EXCEPTION_MIN_R = 0.8            # an unfilled FVG/minor swing in [0.8R, 1.0R) overrides raw TP1
TP1_EXCEPTION_MAX_R = 1.0
TP2_R_MULT = 1.8                     # fallback when no liquidity level sits beyond TP1
# v2: a pooled TP2 level must clear TP1 by at least this much R, else TP1/TP2
# fire on nearly the same candle (80% of the position out at ~1R).
TP2_MIN_SEPARATION_R = 0.5
TP3_R_MULT = 2.8                     # fallback; v2: TP3 = the FARTHER of 2.8R vs. the next external level (was closer)

# v2: expiry is measured in entry-timeframe BARS, not fixed wall-clock minutes,
# so swing mode (1h) no longer expires every order in ~1.5 candles.
PENDING_ORDER_MAX_BARS = 6           # 6 x entry-timeframe bars unfilled -> cancel (EXPIRED)
PENDING_ORDER_MAX_MINUTES = 90       # retained only as a floor/back-compat default (6 x M15)

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
