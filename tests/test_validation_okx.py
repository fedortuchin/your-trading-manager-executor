from __future__ import annotations

from datetime import UTC, datetime

from ytm_executor.validation import validate_broker_credential


class FakeOkxAccountApi:
    def get_account_config(self):
        return {"code": "0", "data": [{"acctLv": "2", "posMode": "net_mode"}], "msg": ""}

    def get_account_balance(self, ccy: str = ""):
        return {"code": "0", "data": [{"details": [{"ccy": "USDT"}]}], "msg": ""}

    def get_positions(self, instType: str = "", instId: str = "", posId: str = ""):
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP"}], "msg": ""}


def test_okx_validate_returns_sanitized_read_only_summary() -> None:
    summary = validate_broker_credential(
        provider="okx",
        name="main",
        secret={
            "apiKey": "okx-public-key",
            "apiSecret": "okx-private-secret",
            "passphrase": "okx-passphrase",
        },
        now=datetime(2026, 5, 10, tzinfo=UTC),
        okx_account_api=FakeOkxAccountApi(),
    )

    assert summary["status"] == "passed"
    assert summary["permissions"] == {
        "accountReadable": True,
        "brokerAccountType": "OKX",
        "market": "okx_swap",
        "positionCount": 1,
        "tradingAllowed": False,
        "tradingPermissionCheck": "order_precheck_required",
        "withdrawalsAllowed": False,
    }
    assert summary["accountFingerprint"].startswith("okx:")
    assert summary["warnings"] == ["trade_permission_not_verified_by_read_only_validation"]
    assert "okx-private-secret" not in repr(summary)
    assert "okx-passphrase" not in repr(summary)


def test_okx_validate_fails_on_rejected_credentials() -> None:
    class RejectedOkxAccountApi:
        def get_account_config(self):
            return {"code": "50113", "data": [], "msg": "invalid sign"}

        def get_account_balance(self, ccy: str = ""):
            raise AssertionError("balance must not be called")

        def get_positions(self, instType: str = "", instId: str = "", posId: str = ""):
            raise AssertionError("positions must not be called")

    summary = validate_broker_credential(
        provider="okx",
        name="main",
        secret={
            "apiKey": "okx-public-key",
            "apiSecret": "okx-private-secret",
            "passphrase": "okx-passphrase",
        },
        now=datetime(2026, 5, 10, tzinfo=UTC),
        okx_account_api=RejectedOkxAccountApi(),
    )

    assert summary["status"] == "failed"
    assert summary["failureReason"] == "credential_rejected"
    assert "okx-private-secret" not in repr(summary)
    assert "okx-passphrase" not in repr(summary)
