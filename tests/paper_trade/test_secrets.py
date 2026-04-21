"""`.env.local` loader tests."""

from __future__ import annotations

import os

from nautilus_trading.paper_trade.secrets import load_dotenv_local


def test_load_dotenv_local_missing_file_is_no_op(tmp_path, monkeypatch):
    """If .env.local is absent, loader returns False and does not raise."""
    monkeypatch.chdir(tmp_path)
    assert load_dotenv_local() is False


def test_load_dotenv_local_populates_env(tmp_path, monkeypatch):
    """When .env.local exists, its keys appear in os.environ."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.local").write_text(
        "BINANCE_TESTNET_API_KEY=tk_abc\nBINANCE_TESTNET_API_SECRET=sk_xyz\n"
    )
    monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)

    assert load_dotenv_local() is True
    assert os.environ["BINANCE_TESTNET_API_KEY"] == "tk_abc"
    assert os.environ["BINANCE_TESTNET_API_SECRET"] == "sk_xyz"


def test_load_dotenv_local_does_not_override_existing(tmp_path, monkeypatch):
    """Existing env vars win over .env.local (so CI secrets aren't overwritten)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.local").write_text("BINANCE_TESTNET_API_KEY=from_file\n")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "from_env")

    load_dotenv_local()
    assert os.environ["BINANCE_TESTNET_API_KEY"] == "from_env"


def test_load_dotenv_local_custom_path(tmp_path, monkeypatch):
    """An explicit path is honored."""
    envfile = tmp_path / "custom.env"
    envfile.write_text("FOO=bar\n")
    monkeypatch.delenv("FOO", raising=False)

    assert load_dotenv_local(path=envfile) is True
    assert os.environ["FOO"] == "bar"
