import strategy_config as cfg


def test_every_instrument_has_a_round_number_offset_entry():
    missing = set(cfg.INSTRUMENTS) - set(cfg.ROUND_NUMBER_OFFSET_TABLE)
    assert missing == set()


def test_every_instrument_has_an_instrument_profile():
    missing = set(cfg.INSTRUMENTS) - set(cfg.INSTRUMENT_PROFILES)
    assert missing == set()


def test_new_asia_pacific_and_correlation_instruments_present():
    for symbol in ("AUDJPY", "AUDUSD", "USDJPY", "JP225", "HK50", "A50"):
        assert symbol in cfg.INSTRUMENTS


def test_active_instruments_cover_the_full_monitored_list():
    assert set(cfg.ACTIVE_INSTRUMENTS) == set(cfg.INSTRUMENTS)
    for symbol in ("US500", "US100", "US30", "BTCUSD", "HK50", "GBPJPY", "A50", "JP225"):
        assert symbol in cfg.ACTIVE_INSTRUMENTS


def test_active_instruments_are_defined_in_the_instrument_table():
    assert set(cfg.ACTIVE_INSTRUMENTS) <= set(cfg.INSTRUMENTS)


def test_v32_alert_thresholds():
    assert cfg.WATCH_MIN_SCORE == 72
    assert cfg.APLUS_MIN_SCORE == 82
    assert cfg.NO_ALERT_MAX == 71
    assert cfg.APLUS_MIN_FLOOR <= cfg.APLUS_BASE_SCORE <= cfg.APLUS_MAX_CAP


def test_v32_setup_validity_is_eight_hours():
    assert cfg.PENDING_ORDER_MAX_MINUTES == 480
    assert cfg.ENTRY_EXPIRY_HOURS == 8


def test_v32_two_fixed_r_targets():
    assert cfg.TP1_R_MULT == 2.0
    assert cfg.TP2_R_MULT == 3.0


def test_correlation_cluster_matches_spec():
    assert cfg.CORRELATION_CLUSTER == {"AUDJPY", "AUDUSD", "USDJPY", "JP225"}


def test_correlation_cluster_members_are_tracked_instruments():
    assert cfg.CORRELATION_CLUSTER <= set(cfg.INSTRUMENTS)


def test_hk50_and_a50_are_asia_index_class():
    assert cfg.INSTRUMENTS["HK50"]["class"] == "ASIA_INDEX"
    assert cfg.INSTRUMENTS["A50"]["class"] == "ASIA_INDEX"


def test_jpy_crosses_use_forex_jpy_class():
    assert cfg.INSTRUMENTS["AUDJPY"]["class"] == "FOREX_JPY"
    assert cfg.INSTRUMENTS["USDJPY"]["class"] == "FOREX_JPY"


def test_audusd_uses_plain_forex_class():
    assert cfg.INSTRUMENTS["AUDUSD"]["class"] == "FOREX"
