"""
Portefeuille fictif ARGOS.

Représente l'état financier du bot en paper trading.
N'interagit jamais avec de l'argent réel.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from argos.utils.logger import get_logger

logger = get_logger("portfolio")

# Capital fictif initial (en EUR) — doit correspondre à risk_rules.yaml
INITIAL_CAPITAL_EUR = 10.0


@dataclass
class Portfolio:
    """
    État du portefeuille paper trading.

    Une seule instance tourne par session scheduler.
    Elle est reconstruite depuis la base de données au démarrage.
    """

    initial_capital: float = INITIAL_CAPITAL_EUR
    current_capital: float = field(init=False)
    realized_pnl: float = 0.0          # P&L des trades clôturés
    total_fees_paid: float = 0.0        # frais cumulés (simulés)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.current_capital = self.initial_capital

    # ------------------------------------------------------------------
    # Métriques calculées
    # ------------------------------------------------------------------

    @property
    def total_return_percent(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return (self.current_capital - self.initial_capital) / self.initial_capital * 100

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100

    @property
    def profit_factor(self) -> float:
        """Ratio gains bruts / pertes brutes. > 1 = système profitable."""
        # Calculé par le LearningAgent depuis la base — ici placeholder
        return 0.0

    # ------------------------------------------------------------------
    # Mutations — appelées uniquement par PaperEngine
    # ------------------------------------------------------------------

    def apply_trade_result(
        self,
        net_pnl: float,
        fees: float,
        won: bool,
    ) -> None:
        """Met à jour le capital après clôture d'un trade."""
        self.current_capital += net_pnl
        self.realized_pnl += net_pnl
        self.total_fees_paid += fees
        self.total_trades += 1
        if won:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

        logger.info(
            f"Portfolio mis a jour : capital={self.current_capital:.4f} EUR  "
            f"P&L={self.realized_pnl:+.4f} EUR  "
            f"trades={self.total_trades} ({self.win_rate:.0f}% win)"
        )

    def summary(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "current_capital": round(self.current_capital, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "total_return_percent": round(self.total_return_percent, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 1),
            "total_fees_paid": round(self.total_fees_paid, 4),
        }
