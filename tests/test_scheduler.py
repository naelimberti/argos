"""
Tests du scheduler ARGOS.

On ne teste jamais APScheduler lui-même — on teste notre logique :
  - run_cycle() appelle bien _execute_cycle()
  - les erreurs sont comptées correctement
  - le verrou empêche deux cycles simultanés
  - les stats de session sont cohérentes
  - _execute_cycle() appelle le provider et stocke les snapshots
"""

import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import argos.scheduler as sched_module
from argos.market_data.base_provider import PriceData, ProviderError


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_scheduler_state():
    """Remet les variables globales du scheduler à zéro avant chaque test."""
    from argos.scheduler import SessionStats
    sched_module._stats = SessionStats()
    sched_module._provider = None
    # S'assurer que le verrou est libre
    if sched_module._cycle_lock.locked():
        sched_module._cycle_lock.release()
    yield
    # Nettoyage post-test
    sched_module._stats = SessionStats()
    sched_module._provider = None


@pytest.fixture
def mock_prices():
    return [
        PriceData(
            asset="BTC", price=55000.0,
            price_change_24h=1.5, volume_24h=25_000_000_000.0,
            high_24h=56000.0, low_24h=54000.0,
            source="coingecko",
            timestamp=datetime.now(timezone.utc),
        ),
    ]


@pytest.fixture
def mock_config():
    return {
        "assets": [
            {"symbol": "BTC", "enabled": True},
        ],
        "scheduler": {
            "scan_interval_minutes": 15,
            "max_consecutive_errors": 3,
        },
    }


# ============================================================
# _get_enabled_assets
# ============================================================

def test_get_enabled_assets_returns_active_only():
    config = {
        "assets": [
            {"symbol": "BTC", "enabled": True},
            {"symbol": "ETH", "enabled": False},
        ]
    }
    result = sched_module._get_enabled_assets(config)
    assert result == ["BTC"]


def test_get_enabled_assets_empty_config():
    assert sched_module._get_enabled_assets({}) == []


# ============================================================
# _get_interval
# ============================================================

def test_get_interval_from_config(mock_config):
    with patch.dict("os.environ", {}, clear=False):
        # S'assurer que la var d'env n'est pas définie
        import os
        os.environ.pop("SCAN_INTERVAL_MINUTES", None)
        result = sched_module._get_interval(mock_config)
    assert result == 15


def test_get_interval_env_overrides_config(mock_config):
    with patch.dict("os.environ", {"SCAN_INTERVAL_MINUTES": "5"}):
        result = sched_module._get_interval(mock_config)
    assert result == 5


def test_get_interval_minimum_is_1(mock_config):
    with patch.dict("os.environ", {"SCAN_INTERVAL_MINUTES": "0"}):
        result = sched_module._get_interval(mock_config)
    assert result == 1


def test_get_interval_invalid_env_falls_back_to_config(mock_config):
    with patch.dict("os.environ", {"SCAN_INTERVAL_MINUTES": "not_a_number"}):
        result = sched_module._get_interval(mock_config)
    assert result == 15


# ============================================================
# SessionStats
# ============================================================

def test_session_stats_initial_state():
    stats = sched_module._stats
    assert stats.total_cycles == 0
    assert stats.successful_cycles == 0
    assert stats.failed_cycles == 0
    assert stats.consecutive_errors == 0
    assert stats.success_rate == 0.0


def test_session_stats_success_rate():
    from argos.scheduler import SessionStats
    stats = SessionStats()
    stats.total_cycles = 10
    stats.successful_cycles = 8
    assert stats.success_rate == pytest.approx(80.0)


def test_session_stats_uptime_positive():
    stats = sched_module._stats
    time.sleep(0.01)
    assert stats.uptime_minutes >= 0


# ============================================================
# run_cycle — comptage des stats
# ============================================================

def test_run_cycle_increments_total_on_success(mock_config, mock_prices, session):
    with patch.object(sched_module, "_load_config", return_value=mock_config), \
         patch.object(sched_module, "_execute_cycle") as mock_exec:
        sched_module.run_cycle()

    assert sched_module._stats.total_cycles == 1
    assert sched_module._stats.successful_cycles == 1
    assert sched_module._stats.failed_cycles == 0
    assert sched_module._stats.consecutive_errors == 0
    mock_exec.assert_called_once_with(1)


def test_run_cycle_increments_failed_on_error(mock_config):
    with patch.object(sched_module, "_load_config", return_value=mock_config), \
         patch.object(sched_module, "_execute_cycle", side_effect=RuntimeError("test error")):
        sched_module.run_cycle()

    assert sched_module._stats.total_cycles == 1
    assert sched_module._stats.successful_cycles == 0
    assert sched_module._stats.failed_cycles == 1
    assert sched_module._stats.consecutive_errors == 1


def test_run_cycle_resets_consecutive_errors_after_success(mock_config):
    sched_module._stats.consecutive_errors = 2

    with patch.object(sched_module, "_load_config", return_value=mock_config), \
         patch.object(sched_module, "_execute_cycle"):
        sched_module.run_cycle()

    assert sched_module._stats.consecutive_errors == 0


def test_run_cycle_multiple_errors_accumulate(mock_config):
    with patch.object(sched_module, "_load_config", return_value=mock_config), \
         patch.object(sched_module, "_execute_cycle", side_effect=RuntimeError("err")):
        sched_module.run_cycle()
        sched_module.run_cycle()

    assert sched_module._stats.consecutive_errors == 2
    assert sched_module._stats.failed_cycles == 2


def test_run_cycle_emergency_stop_on_max_errors(mock_config):
    """Vérifie que SystemExit est levé après N erreurs consécutives."""
    mock_config["scheduler"]["max_consecutive_errors"] = 2

    with patch.object(sched_module, "_load_config", return_value=mock_config), \
         patch.object(sched_module, "_execute_cycle", side_effect=RuntimeError("err")):
        sched_module.run_cycle()  # erreur 1 — pas d'arrêt
        with pytest.raises(SystemExit):
            sched_module.run_cycle()  # erreur 2 — arrêt d'urgence


# ============================================================
# Verrou anti-concurrent
# ============================================================

def test_run_cycle_skips_if_lock_held(mock_config):
    """Si le verrou est déjà pris, le cycle est ignoré sans erreur."""
    sched_module._cycle_lock.acquire()
    try:
        with patch.object(sched_module, "_execute_cycle") as mock_exec:
            sched_module.run_cycle()
        # _execute_cycle ne doit pas avoir été appelé
        mock_exec.assert_not_called()
        # Les stats ne doivent pas avoir bougé
        assert sched_module._stats.total_cycles == 0
    finally:
        sched_module._cycle_lock.release()


def test_run_cycle_lock_released_after_success(mock_config):
    with patch.object(sched_module, "_load_config", return_value=mock_config), \
         patch.object(sched_module, "_execute_cycle"):
        sched_module.run_cycle()

    assert not sched_module._cycle_lock.locked()


def test_run_cycle_lock_released_after_error(mock_config):
    with patch.object(sched_module, "_load_config", return_value=mock_config), \
         patch.object(sched_module, "_execute_cycle", side_effect=RuntimeError("err")):
        sched_module.run_cycle()

    assert not sched_module._cycle_lock.locked()


# ============================================================
# _execute_cycle — pipeline fetch → stockage
# ============================================================

def _make_mock_session():
    """Helper : context manager de session mocké."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=MagicMock())
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_execute_cycle_calls_provider_and_saves(mock_config, mock_prices):
    """Vérifie que le cycle appelle le provider et sauvegarde les snapshots."""
    mock_provider = MagicMock()
    mock_provider.get_prices.return_value = mock_prices
    sched_module._provider = mock_provider

    with patch.object(sched_module, "_load_config", return_value=mock_config), \
         patch.object(sched_module, "get_session", return_value=_make_mock_session()), \
         patch.object(sched_module, "save_market_snapshot") as mock_save:

        sched_module._execute_cycle(cycle_num=1)

    mock_provider.get_prices.assert_called_once_with(["BTC"])
    assert mock_save.call_count == 1


def test_execute_cycle_updates_last_prices(mock_config, mock_prices):
    mock_provider = MagicMock()
    mock_provider.get_prices.return_value = mock_prices
    sched_module._provider = mock_provider

    with patch.object(sched_module, "_load_config", return_value=mock_config), \
         patch.object(sched_module, "get_session", return_value=_make_mock_session()), \
         patch.object(sched_module, "save_market_snapshot"):

        sched_module._execute_cycle(cycle_num=1)

    assert sched_module._stats.last_prices["BTC"] == 55000.0


def test_execute_cycle_raises_on_provider_error(mock_config):
    mock_provider = MagicMock()
    mock_provider.get_prices.side_effect = ProviderError("coingecko", "timeout")
    sched_module._provider = mock_provider

    with patch.object(sched_module, "_load_config", return_value=mock_config):
        with pytest.raises(ProviderError):
            sched_module._execute_cycle(cycle_num=1)


def test_execute_cycle_skips_if_no_assets():
    config = {"assets": [{"symbol": "BTC", "enabled": False}]}
    original_provider = sched_module._provider
    with patch.object(sched_module, "_load_config", return_value=config):
        sched_module._execute_cycle(cycle_num=1)
    # Le provider singleton ne doit pas avoir été créé
    assert sched_module._provider is original_provider


def test_execute_cycle_creates_provider_singleton(mock_config, mock_prices):
    """Le provider ne doit être instancié qu'une seule fois."""
    sched_module._provider = None

    mock_instance = MagicMock()
    mock_instance.get_prices.return_value = mock_prices

    with patch.object(sched_module, "_load_config", return_value=mock_config), \
         patch.object(sched_module, "CoinGeckoProvider", return_value=mock_instance) as mock_cls, \
         patch.object(sched_module, "get_session", return_value=_make_mock_session()), \
         patch.object(sched_module, "save_market_snapshot"):

        sched_module._execute_cycle(cycle_num=1)
        sched_module._execute_cycle(cycle_num=2)

    # CoinGeckoProvider() ne doit avoir été appelé qu'une seule fois
    mock_cls.assert_called_once()
