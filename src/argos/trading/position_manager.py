"""
Position Manager — snapshot de l'état des positions ouvertes.

Fournit une vue en lecture seule des positions pour le dashboard et les logs.
Ne modifie rien directement — c'est le rôle du PaperEngine.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from argos.database.models import PaperTrade, TradeDirection
from argos.database.repositories import get_open_positions
from argos.utils.logger import get_logger

logger = get_logger("position_manager")


@dataclass
class PositionView:
    """Vue lisible d'une position ouverte."""
    trade_id: int
    asset: str
    direction: str
    entry_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    position_size_eur: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    duration_hours: float
    distance_to_sl_pct: float
    distance_to_tp_pct: float


def get_position_views(
    session,
    current_prices: dict[str, float],
) -> list[PositionView]:
    """Retourne une vue enrichie de toutes les positions ouvertes."""
    open_trades = get_open_positions(session)
    views = []

    for trade in open_trades:
        price = current_prices.get(trade.asset)
        if price is None:
            continue

        views.append(_build_view(trade, price))

    return views


def _build_view(trade: PaperTrade, current_price: float) -> PositionView:
    now = datetime.now(timezone.utc)
    entry_time = trade.entry_time
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)
    duration_hours = (now - entry_time).total_seconds() / 3600

    if trade.direction == TradeDirection.LONG:
        unrealized_pnl = (current_price - trade.entry_price) / trade.entry_price * trade.position_size_eur
        distance_sl = (current_price - trade.stop_loss) / current_price * 100
        distance_tp = (trade.take_profit - current_price) / current_price * 100
    else:
        unrealized_pnl = (trade.entry_price - current_price) / trade.entry_price * trade.position_size_eur
        distance_sl = (trade.stop_loss - current_price) / current_price * 100
        distance_tp = (current_price - trade.take_profit) / current_price * 100

    unrealized_pnl_pct = unrealized_pnl / trade.position_size_eur * 100

    return PositionView(
        trade_id=trade.id,
        asset=trade.asset,
        direction=trade.direction.value,
        entry_price=trade.entry_price,
        current_price=current_price,
        stop_loss=trade.stop_loss,
        take_profit=trade.take_profit,
        position_size_eur=trade.position_size_eur,
        unrealized_pnl=round(unrealized_pnl, 6),
        unrealized_pnl_pct=round(unrealized_pnl_pct, 4),
        duration_hours=round(duration_hours, 2),
        distance_to_sl_pct=round(distance_sl, 2),
        distance_to_tp_pct=round(distance_tp, 2),
    )


def log_open_positions(session, current_prices: dict[str, float]) -> None:
    """Log résumé des positions ouvertes — appelé à chaque cycle."""
    views = get_position_views(session, current_prices)
    if not views:
        logger.info("Aucune position ouverte.")
        return

    for v in views:
        sign = "+" if v.unrealized_pnl >= 0 else ""
        logger.info(
            f"[POSITION] {v.asset} {v.direction} | "
            f"Prix={v.current_price:,.2f} EUR | "
            f"P&L non realise={sign}{v.unrealized_pnl:.4f} EUR ({sign}{v.unrealized_pnl_pct:.2f}%) | "
            f"SL a {v.distance_to_sl_pct:.2f}% | TP a {v.distance_to_tp_pct:.2f}% | "
            f"Duree={v.duration_hours:.1f}h"
        )
