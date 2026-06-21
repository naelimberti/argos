"""
Normaliseur de données de marché.

Convertit un PriceData (sorti d'un provider) en MarketSnapshot (enregistré en base).
Point d'entrée unique entre les providers et la base de données.
"""

from argos.database.models import MarketSnapshot
from argos.market_data.base_provider import PriceData


def price_data_to_snapshot(data: PriceData) -> MarketSnapshot:
    """Convertit un PriceData en MarketSnapshot prêt à être enregistré."""
    return MarketSnapshot(
        timestamp=data.timestamp,
        asset=data.asset,
        price=data.price,
        volume_24h=data.volume_24h,
        price_change_24h=data.price_change_24h,
        price_change_1h=data.price_change_1h,  # None si non fourni par le provider
        high_24h=data.high_24h,
        low_24h=data.low_24h,
        volatility_estimate=data.volatility_estimate,
        spread_estimate=data.spread_estimate,
        source=data.source,
    )
