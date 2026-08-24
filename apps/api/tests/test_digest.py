"""Test E6: digest harian - format teks + sanitasi."""

from seith_api.digest import build_daily_digest


class TestBuildDailyDigest:
    def test_contains_all_sections(self):
        text = build_daily_digest(
            environment="paper",
            halted=False,
            mode="semi",
            pending_count=2,
            approved_count=1,
            submitted_today=3,
            upcoming_events=[
                ("09-04 12:30", "EUR_USD", "nonfarm_payrolls"),
                ("09-04 12:30", "BTCUSDT", "cpi"),
            ],
        )
        assert "SEITH Digest Harian" in text
        assert "mode <b>semi</b>" in text
        assert "pending=2" in text
        assert "submitted/filled (total)=3" in text
        assert "Rilis 24 jam ke depan" in text
        assert "nonfarm_payrolls" in text

    def test_no_upcoming_events_message(self):
        text = build_daily_digest(
            environment="paper", halted=True, mode="off",
            pending_count=0, approved_count=0, submitted_today=0,
            upcoming_events=[],
        )
        assert "Tidak ada rilis terjadwal" in text
        assert "🛑 AKTIF" in text

    def test_no_secret_or_account_detail_leak(self):
        text = build_daily_digest(
            environment="paper", halted=False, mode="auto",
            pending_count=1, approved_count=0, submitted_today=0,
            upcoming_events=[("09-05 12:30", "BTCUSDT", "cpi")],
        )
        for forbidden in ("api_key", "token", "ord_", "sig_", "sk-"):
            assert forbidden not in text.lower()
