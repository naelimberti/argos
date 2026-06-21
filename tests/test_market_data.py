"""
Tests de la couche données de marché.

Les tests unitaires mockent les appels HTTP — aucune requête réseau réelle.
Les tests d'intégration (marqués integration) peuvent être lancés séparément.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from argos.market_data.base_provider import PriceData, ProviderError
from argos.market_data.coingecko import CoinGeckoProvider
from argos.market_data.normalizer import price_data_to_snapshot


# ============================================================
# Fixtures
# ============================================================

MOCK_COINGECKO_RESPONSE = {
    "bitcoin": {
        "eur": 95000.0,
        "eur_24h_vol": 28_000_000_000.0,
        "eur_24h_change": 2.35,
        "eur_market_cap": 1_850_000_000_000.0,
        "last_updated_at": 1_700_000_000,
    },
}


@pytest.fixture
def provider():
    return CoinGeckoProvider(api_key=None)


@pytest.fixture
def mock_fetch(provider):
    """Remplace _fetch_with_retry pour ne jamais faire de vrai appel réseau."""
    with patch.object(provider, "_fetch_with_retry", return_value=MOCK_COINGECKO_RESPONSE) as m:
        yield m


# ============================================================
# PriceData
# ============================================================

def test_price_data_spread_estimate_low_volatility():
    data = PriceData(asset="BTC", price=95000.0, price_change_24h=1.5)
    assert data.spread_estimate == 0.05


def test_price_data_spread_estimate_medium_volatility():
    data = PriceData(asset="BTC", price=95000.0, price_change_24h=3.0)
    assert data.spread_estimate == 0.10


def test_price_data_spread_estimate_high_volatility():
    data = PriceData(asset="BTC", price=95000.0, price_change_24h=7.0)
    assert data.spread_estimate == 0.20


def test_price_data_spread_estimate_no_change():
    data = PriceData(asset="BTC", price=95000.0)
    assert data.spread_estimate == 0.10


def test_price_data_volatility_from_range():
    data = PriceData(asset="BTC", price=100_000.0, high_24h=105_000.0, low_24h=95_000.0)
    assert data.volatility_estimate == pytest.approx(10.0)


def test_price_data_volatility_fallback_to_change():
    data = PriceData(asset="BTC", price=95000.0, price_change_24h=-3.5)
    assert data.volatility_estimate == pytest.approx(3.5)


# ============================================================
# CoinGeckoProvider — get_prices
# ============================================================

def test_get_prices_returns_btc(provider, mock_fetch):
    prices = provider.get_prices(["BTC"])
    assert len(prices) == 1
    assert prices[0].asset == "BTC"
    assert prices[0].price == 95000.0


def test_get_prices_maps_metadata(provider, mock_fetch):
    prices = provider.get_prices(["BTC"])
    btc = prices[0]
    assert btc.volume_24h == 28_000_000_000.0
    assert btc.price_change_24h == pytest.approx(2.35)
    assert btc.source == "coingecko"


def test_get_prices_timestamp_from_api(provider, mock_fetch):
    prices = provider.get_prices(["BTC"])
    assert isinstance(prices[0].timestamp, datetime)


def test_get_price_single_asset(provider, mock_fetch):
    price = provider.get_price("BTC")
    assert price.asset == "BTC"
    assert price.price == 95000.0


def test_unknown_asset_raises_provider_error(provider):
    with pytest.raises(ProviderError, match="non supportés"):
        provider.get_prices(["SHIB"])


def test_missing_data_in_response_raises_error(provider):
    with patch.object(provider, "_fetch_with_retry", return_value={}):
        with pytest.raises(ProviderError, match="Données manquantes"):
            provider.get_prices(["BTC"])


def test_http_429_triggers_retry(provider):
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429

    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.json.return_value = MOCK_COINGECKO_RESPONSE

    with patch.object(provider._session, "get", side_effect=[mock_resp_429, mock_resp_ok]):
        with patch("time.sleep"):  # ne pas attendre réellement
            prices = provider.get_prices(["BTC"])
    assert prices[0].price == 95000.0


def test_network_error_raises_after_retries(provider):
    import requests
    with patch.object(
        provider._session, "get",
        side_effect=requests.exceptions.ConnectionError("no network")
    ):
        with patch("time.sleep"):
            with pytest.raises(ProviderError):
                provider.get_prices(["BTC"])


# ============================================================
# Normalizer
# ============================================================

def test_normalizer_creates_snapshot_from_price_data():
    data = PriceData(
        asset="BTC",
        price=95000.0,
        volume_24h=28_000_000_000.0,
        price_change_24h=2.35,
        high_24h=97000.0,
        low_24h=93000.0,
        source="coingecko",
    )
    snapshot = price_data_to_snapshot(data)

    assert snapshot.asset == "BTC"
    assert snapshot.price == 95000.0
    assert snapshot.volume_24h == 28_000_000_000.0
    assert snapshot.price_change_24h == pytest.approx(2.35)
    assert snapshot.source == "coingecko"
    # spread et volatilité calculés automatiquement
    assert snapshot.spread_estimate is not None
    assert snapshot.volatility_estimate is not None


def test_normalizer_handles_missing_optional_fields():
    data = PriceData(asset="BTC", price=95000.0)
    snapshot = price_data_to_snapshot(data)
    assert snapshot.volume_24h is None
    assert snapshot.price_change_24h is None
    assert snapshot.asset == "BTC"


# ============================================================
# Intégration DB : snapshot sauvegardé après normalisation
# ============================================================

def test_snapshot_saved_to_database(session, provider, mock_fetch):
    """Vérifie le pipeline complet : fetch → normalise → sauvegarde."""
    from argos.database.repositories import save_market_snapshot, get_last_market_snapshot

    prices = provider.get_prices(["BTC"])
    for price_data in prices:
        snapshot = price_data_to_snapshot(price_data)
        save_market_snapshot(session, snapshot)
    session.commit()

    btc = get_last_market_snapshot(session, "BTC")
    assert btc is not None and btc.price == 95000.0
    assert btc.source == "coingecko"


# ============================================================
# Test marqué integration — requête réseau réelle (optionnel)
# Lancer avec : pytest -m integration
# ============================================================

@pytest.mark.integration
def test_real_coingecko_call():
    """Appel réseau réel vers CoinGecko. Nécessite une connexion internet."""
    provider = CoinGeckoProvider()
    prices = provider.get_prices(["BTC"])
    assert len(prices) == 1
    assert prices[0].price > 0
    print(f"\nBTC : {prices[0].price:,.0f} EUR")
