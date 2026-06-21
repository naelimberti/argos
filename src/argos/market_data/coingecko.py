"""
Provider CoinGecko pour ARGOS.

Utilise l'API publique CoinGecko (gratuite, sans clé pour le plan de base).
Rate limit : ~10-30 appels/minute — largement suffisant pour un cycle de 15 min.

Endpoint utilisé :
  GET /simple/price?ids=bitcoin,ethereum&vs_currencies=eur
      &include_24hr_vol=true&include_24hr_change=true
      &include_last_updated_at=true&include_market_cap=true

Documentation : https://docs.coingecko.com/reference/simple-price
"""

import os
import time
from datetime import datetime
from typing import Optional

import requests

from argos.market_data.base_provider import BaseMarketProvider, PriceData, ProviderError
from argos.utils.logger import get_logger

logger = get_logger("coingecko")

# Correspondance symbole ARGOS → id CoinGecko
ASSET_TO_COINGECKO_ID: dict[str, str] = {
    "BTC": "bitcoin",
}

# URL de base de l'API
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

# Timeout des requêtes HTTP
REQUEST_TIMEOUT_SECONDS = 15

# Délai entre deux tentatives en cas d'erreur
RETRY_DELAY_SECONDS = 5


class CoinGeckoProvider(BaseMarketProvider):
    """Fournisseur de prix via l'API publique CoinGecko."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or os.getenv("COINGECKO_API_KEY") or None
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "ARGOS-PaperTrading/0.1",
        })
        if self._api_key:
            # Plan Pro : header dédié
            self._session.headers["x-cg-pro-api-key"] = self._api_key
            logger.debug("CoinGecko : clé API Pro configurée.")
        else:
            logger.debug("CoinGecko : mode public (sans clé API).")

    @property
    def name(self) -> str:
        return "coingecko"

    def get_price(self, asset: str) -> PriceData:
        """Récupère le prix d'un seul actif."""
        results = self.get_prices([asset])
        return results[0]

    def get_prices(self, assets: list[str]) -> list[PriceData]:
        """Récupère les prix de plusieurs actifs en un seul appel API.

        Raises:
            ProviderError: si l'actif est inconnu ou si l'API est indisponible.
        """
        # Valider les actifs demandés
        unknown = [a for a in assets if a.upper() not in ASSET_TO_COINGECKO_ID]
        if unknown:
            raise ProviderError(self.name, f"Actifs non supportés : {unknown}")

        coingecko_ids = [ASSET_TO_COINGECKO_ID[a.upper()] for a in assets]
        ids_param = ",".join(coingecko_ids)

        params = {
            "ids": ids_param,
            "vs_currencies": "eur",
            "include_24hr_vol": "true",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
            "include_market_cap": "true",
        }

        raw = self._fetch_with_retry(
            endpoint="/simple/price",
            params=params,
        )

        results: list[PriceData] = []
        id_to_asset = {v: k for k, v in ASSET_TO_COINGECKO_ID.items()}

        for cg_id in coingecko_ids:
            if cg_id not in raw:
                raise ProviderError(self.name, f"Données manquantes pour '{cg_id}' dans la réponse.")

            data = raw[cg_id]
            asset_symbol = id_to_asset[cg_id]

            price_data = PriceData(
                asset=asset_symbol,
                price=data["eur"],
                volume_24h=data.get("eur_24h_vol"),
                price_change_24h=data.get("eur_24h_change"),
                market_cap=data.get("eur_market_cap"),
                source=self.name,
                timestamp=datetime.utcfromtimestamp(data["last_updated_at"])
                if "last_updated_at" in data
                else datetime.utcnow(),
            )

            logger.info(
                f"{asset_symbol} : {price_data.price:,.2f} EUR  "
                f"({price_data.price_change_24h:+.2f}% 24h)"
                if price_data.price_change_24h is not None
                else f"{asset_symbol} : {price_data.price:,.2f} EUR"
            )
            results.append(price_data)

        return results

    def get_ohlcv_history(
        self,
        asset: str,
        days: int = 90,
    ) -> list[dict]:
        """Récupère l'historique OHLCV pour le backtesting.

        Retourne une liste de dicts :
          {"timestamp": datetime, "open": float, "high": float,
           "low": float, "close": float, "volume": float}

        Note : l'endpoint /market_chart retourne des données agrégées par jour
        pour les périodes > 90 jours. Pour <= 90 jours, granularité horaire.
        """
        asset_upper = asset.upper()
        if asset_upper not in ASSET_TO_COINGECKO_ID:
            raise ProviderError(self.name, f"Actif non supporté : {asset}")

        cg_id = ASSET_TO_COINGECKO_ID[asset_upper]

        # Utiliser /coins/{id}/ohlc pour des données OHLC propres
        # Valeurs acceptées pour days : 1, 7, 14, 30, 90, 180, 365, max
        allowed_days = [1, 7, 14, 30, 90, 180, 365]
        days_param = min(allowed_days, key=lambda x: abs(x - days))

        raw = self._fetch_with_retry(
            endpoint=f"/coins/{cg_id}/ohlc",
            params={"vs_currency": "eur", "days": days_param},
        )

        # Format retourné : [[timestamp_ms, open, high, low, close], ...]
        results = []
        for candle in raw:
            ts_ms, open_, high, low, close = candle
            results.append({
                "timestamp": datetime.utcfromtimestamp(ts_ms / 1000),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": None,  # non disponible dans cet endpoint
                "asset": asset_upper,
            })

        logger.info(f"Historique OHLCV {asset_upper} : {len(results)} bougies ({days_param} jours)")
        return results

    def check_availability(self) -> bool:
        """Vérifie que l'API CoinGecko est accessible (ping)."""
        try:
            resp = self._session.get(
                f"{COINGECKO_BASE_URL}/ping",
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _fetch_with_retry(
        self,
        endpoint: str,
        params: dict,
        max_retries: int = 3,
    ) -> dict | list:
        """Effectue la requête GET avec retry automatique.

        Gère les erreurs réseau et les rate limits (HTTP 429).
        """
        url = f"{COINGECKO_BASE_URL}{endpoint}"
        last_error: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)

                if resp.status_code == 429:
                    # Rate limit atteint — attendre plus longtemps
                    wait = RETRY_DELAY_SECONDS * attempt * 2
                    logger.warning(f"CoinGecko rate limit (429). Attente {wait}s avant retry {attempt}/{max_retries}.")
                    time.sleep(wait)
                    continue

                if resp.status_code != 200:
                    raise ProviderError(self.name, f"Réponse inattendue", status_code=resp.status_code)

                data = resp.json()

                if not data:
                    raise ProviderError(self.name, "Réponse vide de l'API CoinGecko.")

                return data

            except ProviderError:
                raise
            except requests.exceptions.Timeout:
                last_error = ProviderError(self.name, f"Timeout après {REQUEST_TIMEOUT_SECONDS}s")
                logger.warning(f"CoinGecko timeout (tentative {attempt}/{max_retries})")
            except requests.exceptions.ConnectionError as e:
                last_error = ProviderError(self.name, f"Erreur de connexion : {e}")
                logger.warning(f"CoinGecko connexion échouée (tentative {attempt}/{max_retries})")
            except Exception as e:
                last_error = ProviderError(self.name, f"Erreur inattendue : {e}")
                logger.warning(f"CoinGecko erreur (tentative {attempt}/{max_retries}) : {e}")

            if attempt < max_retries:
                time.sleep(RETRY_DELAY_SECONDS)

        raise last_error or ProviderError(self.name, "Échec après tous les retries.")
