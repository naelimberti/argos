"""
Interface abstraite pour les fournisseurs de données de marché.

Tout provider (CoinGecko, Binance, etc.) doit implémenter cette interface.
Cela permet de switcher de provider sans toucher au reste du code.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class PriceData:
    """Prix et métriques de marché pour un actif à un instant donné."""

    asset: str                          # "BTC", "ETH"
    price: float                        # prix en EUR
    timestamp: datetime = field(default_factory=datetime.utcnow)

    volume_24h: Optional[float] = None          # volume en USD sur 24h
    price_change_1h: Optional[float] = None     # variation % sur 1h
    price_change_24h: Optional[float] = None    # variation % sur 24h
    high_24h: Optional[float] = None            # plus haut sur 24h
    low_24h: Optional[float] = None             # plus bas sur 24h
    market_cap: Optional[float] = None

    source: str = "unknown"

    @property
    def spread_estimate(self) -> float:
        """Spread estimé conservateur basé sur la volatilité 24h."""
        if self.price_change_24h is None:
            return 0.10  # défaut 0.10%
        volatility = abs(self.price_change_24h)
        # Plus c'est volatile, plus le spread est large
        if volatility < 2.0:
            return 0.05
        elif volatility < 5.0:
            return 0.10
        else:
            return 0.20

    @property
    def volatility_estimate(self) -> Optional[float]:
        """Volatilité estimée à partir du range 24h."""
        if self.high_24h and self.low_24h and self.price > 0:
            return (self.high_24h - self.low_24h) / self.price * 100
        return abs(self.price_change_24h) if self.price_change_24h else None


class BaseMarketProvider(ABC):
    """Interface que tout fournisseur de données doit implémenter."""

    @abstractmethod
    def get_price(self, asset: str) -> PriceData:
        """Récupère le prix actuel d'un actif.

        Args:
            asset: symbole de l'actif ("BTC", "ETH")

        Returns:
            PriceData avec le prix et les métriques disponibles

        Raises:
            ProviderError: si la requête échoue
        """

    @abstractmethod
    def get_prices(self, assets: list[str]) -> list[PriceData]:
        """Récupère les prix de plusieurs actifs en un seul appel."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Nom du provider (pour les logs)."""


class ProviderError(Exception):
    """Erreur levée quand un provider de données est indisponible ou retourne une erreur."""

    def __init__(self, provider: str, message: str, status_code: Optional[int] = None):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}" + (f" (HTTP {status_code})" if status_code else ""))
