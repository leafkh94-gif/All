import strategy_config as cfg


def test_gold_is_the_only_instrument():
    assert set(cfg.INSTRUMENTS) == {"XAUUSD"}
    assert cfg.INSTRUMENTS["XAUUSD"]["class"] == "COMMODITY"


def test_active_instruments_are_gold_only():
    assert cfg.ACTIVE_INSTRUMENTS == ["XAUUSD"]


def test_every_instrument_has_a_round_number_offset_entry():
    missing = set(cfg.INSTRUMENTS) - set(cfg.ROUND_NUMBER_OFFSET_TABLE)
    assert missing == set()


def test_every_instrument_has_an_instrument_profile():
    missing = set(cfg.INSTRUMENTS) - set(cfg.INSTRUMENT_PROFILES)
    assert missing == set()


def test_alert_only_flag_still_set():
    assert cfg.ALERT_ONLY is True


def test_golden_trio_constants_are_wired():
    assert cfg.GT_RSI_PERIOD == 14
    assert cfg.GT_RSI_DIP_LOOKBACK > 0
    assert cfg.GT_RSI_MIN_HOOK > 0
    assert 0 < cfg.GT_RSI_BUY_FLOOR < 50
    assert cfg.GT_ZLSMA_PERIOD == 30
    assert cfg.GT_TURTLE_PERIOD >= 5
    assert cfg.GT_CHOP_MIN_RANGE_ATR > 0


def test_score_budget_thresholds_are_consistent():
    # Base-0 model: WATCH threshold must be reachable from component
    # maxes, and A+ threshold must be higher than WATCH.
    max_score = (cfg.SCORE_RSI_CONFIRM_MAX + cfg.SCORE_TURTLE_MAX
                 + cfg.SCORE_ZLSMA_ALIGNED + cfg.SCORE_H4_ALIGNED
                 + cfg.SCORE_KILLZONE_MAX + cfg.SCORE_ROUND_NUMBER)
    assert cfg.WATCH_MIN_SCORE < cfg.APLUS_MIN_SCORE <= max_score


def test_cooldown_constants_are_positive():
    assert cfg.COOLDOWN_SAME_DIRECTION_MINUTES > 0
    assert cfg.COOLDOWN_OPPOSITE_DIRECTION_MINUTES > 0
    assert cfg.COOLDOWN_SAME_DIRECTION_POINTS > 0


# ─────────────────────────────────────────────────────────────────────
# Tier coherence. A+ at 70 was carried unchanged through a scoring
# rewrite and ended up above the practical score ceiling, so the tier
# went silent. These pin the relationships that must hold whatever the
# numbers are.
# ─────────────────────────────────────────────────────────────────────

def test_tiers_are_ordered_and_contiguous():
    assert cfg.NO_ALERT_MAX < cfg.WATCH_MIN_SCORE
    assert cfg.WATCH_MIN_SCORE <= cfg.WATCH_MAX_SCORE
    assert cfg.WATCH_MAX_SCORE == cfg.APLUS_MIN_SCORE - 1, \
        "WATCH must end exactly where A+ begins, or scores fall in a gap"
    assert cfg.NO_ALERT_MAX == cfg.WATCH_MIN_SCORE - 1


def test_watch_upgrade_and_collapse_line_up_with_the_tiers():
    assert cfg.WATCH_UPGRADE_SCORE == cfg.APLUS_MIN_SCORE
    assert cfg.WATCH_COLLAPSE_SCORE <= cfg.WATCH_MIN_SCORE


def test_aplus_sits_inside_the_reachable_score_range():
    """The score budget must be able to exceed the A+ line by a real
    margin. Setting A+ within a few points of the ceiling is what made
    the tier fire on 0.7% of signals."""
    reachable = (cfg.SCORE_SETUP_MAX + cfg.SCORE_ZLSMA_ALIGNED
                 + cfg.SCORE_H4_MAX + cfg.SCORE_H1_MAX
                 + cfg.SCORE_M5_MAX + cfg.SCORE_M1_MAX
                 + cfg.SCORE_ROUND_NUMBER)
    assert cfg.APLUS_MIN_SCORE < reachable * 0.75, (
        f"A+ ({cfg.APLUS_MIN_SCORE}) is too close to the {reachable}-point "
        "ceiling; in practice components rarely saturate together")


def test_atr_stop_stays_well_clear_of_the_spread():
    """Gold's spread is large relative to M15 noise. If the stop is not
    comfortably wider than the worst accepted spread, cost dominates."""
    min_stop = cfg.ATR_SL_MIN_POINTS * cfg.POINT_VALUE
    assert min_stop >= cfg.MAX_SPREAD_POINTS * 6, \
        "worst-case spread is more than ~17% of the tightest allowed stop"


def test_atr_ladder_is_ordered():
    assert 0 < cfg.ATR_TP1_R < cfg.ATR_TP2_R < cfg.ATR_TP3_R
    assert cfg.ATR_SL_MIN_POINTS < cfg.ATR_SL_MAX_POINTS
