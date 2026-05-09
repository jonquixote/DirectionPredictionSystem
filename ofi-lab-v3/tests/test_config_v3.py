import config


def test_storage_db_path_default():
    assert config.STORAGE_DB_PATH == "/data/v3.db"


def test_evaluation_windows_complete():
    # All four live market windows; lifecycle code subtracts the native
    # horizon at runtime.
    assert sorted(config.EVALUATION_WINDOWS) == [300, 900, 1800, 3600]


def test_baseline_model_name_constant():
    assert config.BASELINE_MODEL_NAME == "900s_btc_v3_20260315"


def test_baseline_protected_flag():
    assert config.BASELINE_PROTECTED is True


def test_warmup_seconds_default():
    assert config.WARMUP_SECONDS == 1800  # 30 minutes
