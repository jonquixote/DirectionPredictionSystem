import json
import sqlite3
import pytest


@pytest.fixture
def db_with_selection(tmp_path):
    db_path = tmp_path / "v3.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    from storage.db import init_schema
    init_schema(conn)

    for name in ["h300_btc_89d", "h300_btc_179d", "h300_btc_329d"]:
        conn.execute(
            "INSERT INTO model_registry (name, symbol, training_horizon_seconds, artifact_path) "
            "VALUES (?,?,?,?)",
            (name, "BTCUSDT", 300, "/tmp/model.lgb"),
        )

    conn.execute(
        "INSERT INTO model_selection (symbol, market_window_seconds, strategy, selected_model_name) "
        "VALUES (?,?,?,?)",
        ("BTCUSDT", 300, "single_model", "h300_btc_179d"),
    )
    conn.commit()
    yield conn
    conn.close()


def test_single_model_strategy_selects_one_model(db_with_selection):
    from trading.model_selector import ModelSelector
    selector = ModelSelector(db_with_selection)
    result = selector.select("BTCUSDT", 300, ["h300_btc_89d", "h300_btc_179d", "h300_btc_329d"])
    assert result.strategy == "single_model"
    assert result.selected == ["h300_btc_179d"]
    assert result.blocked == ["h300_btc_89d", "h300_btc_329d"]


def test_committee_weighted_strategy_selects_all(db_with_selection):
    conn = db_with_selection
    conn.execute(
        "UPDATE model_selection SET strategy='committee_weighted', selected_model_name=NULL "
        "WHERE symbol='BTCUSDT' AND market_window_seconds=300",
    )
    conn.commit()

    from trading.model_selector import ModelSelector
    selector = ModelSelector(conn)
    result = selector.select("BTCUSDT", 300, ["h300_btc_89d", "h300_btc_179d", "h300_btc_329d"])
    assert result.strategy == "committee_weighted"
    assert result.selected == ["h300_btc_89d", "h300_btc_179d", "h300_btc_329d"]
    assert result.blocked == []


def test_best_ev_strategy_selects_highest_ev(db_with_selection):
    conn = db_with_selection
    conn.execute(
        "UPDATE model_selection SET strategy='best_ev', selected_model_name=NULL "
        "WHERE symbol='BTCUSDT' AND market_window_seconds=300",
    )
    conn.execute(
        "INSERT INTO decay_metrics (ts, model_name, symbol, market_window_seconds, "
        "window_size, rolling_ev, recency_weighted_ev, rolling_win_rate, "
        "brier_score, calibration_error, sample_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-05-14T00:00:00Z", "h300_btc_89d", "BTCUSDT", 300, 100, 0.01, 0.015, 0.52, 0.24, 0.03, 100),
    )
    conn.execute(
        "INSERT INTO decay_metrics (ts, model_name, symbol, market_window_seconds, "
        "window_size, rolling_ev, recency_weighted_ev, rolling_win_rate, "
        "brier_score, calibration_error, sample_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-05-14T00:00:00Z", "h300_btc_179d", "BTCUSDT", 300, 100, 0.03, 0.035, 0.55, 0.22, 0.02, 100),
    )
    conn.execute(
        "INSERT INTO decay_metrics (ts, model_name, symbol, market_window_seconds, "
        "window_size, rolling_ev, recency_weighted_ev, rolling_win_rate, "
        "brier_score, calibration_error, sample_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-05-14T00:00:00Z", "h300_btc_329d", "BTCUSDT", 300, 100, 0.02, 0.025, 0.53, 0.23, 0.025, 100),
    )
    conn.commit()

    from trading.model_selector import ModelSelector
    selector = ModelSelector(conn)
    result = selector.select("BTCUSDT", 300, ["h300_btc_89d", "h300_btc_179d", "h300_btc_329d"])
    assert result.strategy == "best_ev"
    assert result.selected == ["h300_btc_179d"]
    assert set(result.blocked) == {"h300_btc_89d", "h300_btc_329d"}


def test_disabled_strategy_blocks_all(db_with_selection):
    conn = db_with_selection
    conn.execute(
        "UPDATE model_selection SET strategy='disabled', selected_model_name=NULL "
        "WHERE symbol='BTCUSDT' AND market_window_seconds=300",
    )
    conn.commit()

    from trading.model_selector import ModelSelector
    selector = ModelSelector(conn)
    result = selector.select("BTCUSDT", 300, ["h300_btc_89d", "h300_btc_179d", "h300_btc_329d"])
    assert result.strategy == "disabled"
    assert result.selected == []
    assert result.blocked == ["h300_btc_89d", "h300_btc_179d", "h300_btc_329d"]


def test_no_selection_row_defaults_to_all_models(db_with_selection):
    from trading.model_selector import ModelSelector
    selector = ModelSelector(db_with_selection)
    result = selector.select("ETHUSDT", 300, ["h300_eth_89d"])
    assert result.strategy == "all"
    assert result.selected == ["h300_eth_89d"]
