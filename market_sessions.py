"""
Trading Alert Bot — ICT Killzone session scoring.
Replaces the broad London/NY/Asia session scoring (Section 5.2). All times UTC.
"""
from datetime import time

# (name, start, end, bonus_us_index, bonus_btc) — end is exclusive, times are UTC.
KILLZONES = [
    ("ASIAN_SESSION",        time(0, 0),  time(6, 0),  2,  3),
    ("LONDON_PRE_KILL",      time(6, 0),  time(7, 0),  6,  4),
    ("LONDON_KILLZONE",      time(7, 0),  time(8, 30), 12, 6),
    ("NY_PRE_MARKET",        time(11, 30), time(12, 30), 6, 6),
    ("NY_KILLZONE",          time(12, 30), time(14, 0), 12, 10),
]

DEAD_ZONE_PENALTY_US_INDEX = -4
DEAD_ZONE_PENALTY_BTC = -2


def killzone_bonus(now_utc, instrument_class):
    """Return (bonus_points, zone_name) for the given UTC time and instrument class
    ('US_INDEX' or 'CRYPTO'). Falls back to the dead-zone penalty outside all windows."""
    t = now_utc.time()
    for name, start, end, bonus_index, bonus_btc in KILLZONES:
        if start <= t < end:
            return (bonus_index, name) if instrument_class == "US_INDEX" else (bonus_btc, name)
    penalty = DEAD_ZONE_PENALTY_US_INDEX if instrument_class == "US_INDEX" else DEAD_ZONE_PENALTY_BTC
    return penalty, "DEAD_ZONE"
