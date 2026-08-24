"""Test E4: saklar mode trading off/semi/auto di shared DB."""

import pytest
from seith_core.config import AppSettings

from seith_data.trading_mode import (
    TradingMode,
    get_trading_mode,
    init_trading_mode_db,
    set_trading_mode,
)


@pytest.fixture()
def settings(tmp_path):
    return AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "test.db",
    )


class TestTradingMode:
    def test_default_is_semi(self, settings):
        mode, min_conf, notional = get_trading_mode(settings)
        assert mode is TradingMode.SEMI  # Tier-0 friendly default
        assert 0 < min_conf <= 1
        assert notional > 0

    def test_set_and_persist_round_trip(self, settings):
        set_trading_mode(TradingMode.AUTO, updated_by="telegram:123",
                         auto_min_confidence=0.8, settings=settings)
        # koneksi baru (fungsi berikutnya buka koneksi sendiri)
        mode, min_conf, _ = get_trading_mode(settings)
        assert mode is TradingMode.AUTO
        assert min_conf == pytest.approx(0.8)

    def test_set_semi_keeps_existing_threshold(self, settings):
        set_trading_mode(TradingMode.AUTO, updated_by="t", auto_min_confidence=0.9,
                         settings=settings)
        set_trading_mode(TradingMode.SEMI, updated_by="t", settings=settings)
        _, min_conf, _ = get_trading_mode(settings)
        assert min_conf == pytest.approx(0.9)  # threshold tidak ikut tereset

    def test_all_modes_accepted(self, settings):
        for m in TradingMode:
            set_trading_mode(m, updated_by="t", settings=settings)
            assert get_trading_mode(settings)[0] is m

    def test_init_idempotent_no_duplicate_row(self, settings):
        init_trading_mode_db(settings)
        init_trading_mode_db(settings)
        import sqlite3

        with sqlite3.connect(settings.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM trading_mode").fetchone()[0]
        assert count == 1  # satu-satunya baris (id=1), tidak terduplikasi
