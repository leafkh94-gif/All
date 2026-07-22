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


def test_correlation_clusters_match_spec():
    sets = [members for _, members in cfg.CORRELATION_CLUSTERS]
    assert {"AUDJPY", "AUDUSD", "USDJPY", "JP225"} in sets
    assert {"US500", "US100", "US30"} in sets   # v2: US indices are one move


def test_correlation_cluster_members_are_tracked_instruments():
    for _, members in cfg.CORRELATION_CLUSTERS:
        assert members <= set(cfg.INSTRUMENTS)


def test_correlation_cluster_of_lookup():
    assert cfg.correlation_cluster_of("US100")[1] == {"US500", "US100", "US30"}
    assert cfg.correlation_cluster_of("AUDJPY")[1] == {"AUDJPY", "AUDUSD", "USDJPY", "JP225"}
    assert cfg.correlation_cluster_of("BTCUSD") is None


def test_hk50_and_a50_are_asia_index_class():
    assert cfg.INSTRUMENTS["HK50"]["class"] == "ASIA_INDEX"
    assert cfg.INSTRUMENTS["A50"]["class"] == "ASIA_INDEX"


def test_jpy_crosses_use_forex_jpy_class():
    assert cfg.INSTRUMENTS["AUDJPY"]["class"] == "FOREX_JPY"
    assert cfg.INSTRUMENTS["USDJPY"]["class"] == "FOREX_JPY"


def test_audusd_uses_plain_forex_class():
    assert cfg.INSTRUMENTS["AUDUSD"]["class"] == "FOREX"
