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
    assert cfg.GT_RSI_OVERSOLD < cfg.GT_RSI_CONFIRM_LEVEL < cfg.GT_RSI_OVERBOUGHT
    assert cfg.GT_ZLSMA_PERIOD == 30
    assert cfg.GT_TURTLE_PERIOD == 20
