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
