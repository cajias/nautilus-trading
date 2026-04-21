"""Preflight tests for _check_testnet_api_keys()."""

from __future__ import annotations

import pytest
from nautilus_trading.paper_trade.node_config import _check_testnet_api_keys


@pytest.fixture
def clean_env(monkeypatch):
    for var in (
        "BINANCE_TESTNET_API_KEY",
        "BINANCE_TESTNET_API_SECRET",
        "BINANCE_TESTNET_ED25519_KEY_PATH",
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_missing_api_key_exits(clean_env, capsys):
    with pytest.raises(SystemExit) as exc:
        _check_testnet_api_keys()
    assert exc.value.code == 1
    err = capsys.readouterr().out
    assert "BINANCE_TESTNET_API_KEY" in err


def test_missing_api_secret_exits(clean_env, capsys):
    clean_env.setenv("BINANCE_TESTNET_API_KEY", "dummy")
    with pytest.raises(SystemExit) as exc:
        _check_testnet_api_keys()
    assert exc.value.code == 1
    err = capsys.readouterr().out
    assert "BINANCE_TESTNET_API_SECRET" in err


def test_missing_ed25519_path_exits(clean_env, capsys):
    clean_env.setenv("BINANCE_TESTNET_API_KEY", "dummy")
    clean_env.setenv("BINANCE_TESTNET_API_SECRET", "dummy")
    with pytest.raises(SystemExit) as exc:
        _check_testnet_api_keys()
    assert exc.value.code == 1
    err = capsys.readouterr().out
    assert "BINANCE_TESTNET_ED25519_KEY_PATH" in err


def test_unreadable_ed25519_path_exits(clean_env, tmp_path, capsys):
    missing = tmp_path / "nonexistent.pem"
    clean_env.setenv("BINANCE_TESTNET_API_KEY", "dummy")
    clean_env.setenv("BINANCE_TESTNET_API_SECRET", "dummy")
    clean_env.setenv("BINANCE_TESTNET_ED25519_KEY_PATH", str(missing))
    with pytest.raises(SystemExit) as exc:
        _check_testnet_api_keys()
    assert exc.value.code == 1
    err = capsys.readouterr().out
    assert "not readable" in err.lower() or "does not exist" in err.lower()


def test_all_present_passes(clean_env, tmp_path):
    pem = tmp_path / "test_ed25519.pem"
    pem.write_text("-----BEGIN PRIVATE KEY-----\ndummy\n-----END PRIVATE KEY-----\n")
    clean_env.setenv("BINANCE_TESTNET_API_KEY", "dummy")
    clean_env.setenv("BINANCE_TESTNET_API_SECRET", "dummy")
    clean_env.setenv("BINANCE_TESTNET_ED25519_KEY_PATH", str(pem))
    _check_testnet_api_keys()  # Should not raise
