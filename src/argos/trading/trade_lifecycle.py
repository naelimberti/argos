"""
Cycle de vie d'un trade simulé.

Fonctions utilitaires pour calculer les métriques d'un trade
sans accès direct à la base — utilisées par les tests et le Learning Agent.
"""

from dataclasses import dataclass

from argos.database.models import TradeDirection


@dataclass
class TradeMetrics:
    """Métriques calculées d'un trade (avant ou après clôture)."""
    risk_eur: float           # perte maximale possible en EUR
    reward_eur: float         # gain maximal possible en EUR
    risk_reward_ratio: float  # reward / risk
    risk_pct: float           # risque en % du capital
    breakeven_fee_pct: float  # % de mouvement nécessaire pour couvrir les frais
    net_expected_value: float # espérance nette (simplifié)


def compute_trade_metrics(
    direction: TradeDirection,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    position_size_eur: float,
    capital_eur: float,
    fee_pct: float = 0.002,       # 0.20% aller-retour
    spread_pct: float = 0.001,
    slippage_pct: float = 0.0005,
) -> TradeMetrics:
    """Calcule les métriques d'un trade avant son ouverture."""
    if direction == TradeDirection.LONG:
        risk_price = entry_price - stop_loss
        reward_price = take_profit - entry_price
    else:
        risk_price = stop_loss - entry_price
        reward_price = entry_price - take_profit

    risk_eur = (risk_price / entry_price) * position_size_eur
    reward_eur = (reward_price / entry_price) * position_size_eur

    total_friction_eur = position_size_eur * (fee_pct + spread_pct + slippage_pct)

    rr_ratio = reward_eur / risk_eur if risk_eur > 0 else 0.0
    risk_pct_capital = risk_eur / capital_eur * 100 if capital_eur > 0 else 0.0
    breakeven_pct = (fee_pct + spread_pct + slippage_pct) * 100

    # Espérance nette simplifiée (win_rate=50% supposé)
    net_ev = (reward_eur - total_friction_eur) * 0.5 - (risk_eur + total_friction_eur) * 0.5

    return TradeMetrics(
        risk_eur=round(risk_eur, 6),
        reward_eur=round(reward_eur, 6),
        risk_reward_ratio=round(rr_ratio, 4),
        risk_pct=round(risk_pct_capital, 4),
        breakeven_fee_pct=round(breakeven_pct, 4),
        net_expected_value=round(net_ev, 6),
    )


def describe_trade(
    asset: str,
    direction: TradeDirection,
    entry: float,
    sl: float,
    tp: float,
    size_eur: float,
    capital_eur: float,
) -> str:
    """Retourne une description lisible d'un trade proposé."""
    metrics = compute_trade_metrics(direction, entry, sl, tp, size_eur, capital_eur)
    dir_str = direction.value

    return (
        f"{asset} {dir_str} | "
        f"Entree={entry:,.2f} SL={sl:,.2f} TP={tp:,.2f} | "
        f"Taille={size_eur:.4f} EUR | "
        f"Risque={metrics.risk_eur:.4f} EUR ({metrics.risk_pct:.2f}% du capital) | "
        f"R/R={metrics.risk_reward_ratio:.2f} | "
        f"Seuil rentabilite={metrics.breakeven_fee_pct:.3f}%"
    )
