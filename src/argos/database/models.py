"""
Modèles SQLModel pour ARGOS.

Chaque classe = une table SQLite.
Les types sont stricts pour éviter les données corrompues en base.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


# ============================================================
# Enums — valeurs contrôlées
# ============================================================


class TradeDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeResult(str, Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    OPEN = "OPEN"
    TIMEOUT = "TIMEOUT"
    REJECTED = "REJECTED"


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class RiskDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class TradeCategory(str, Enum):
    CLEAN_WIN = "clean_win"
    LUCKY_WIN = "lucky_win"
    NORMAL_LOSS = "normal_loss"
    AVOIDABLE_LOSS = "avoidable_loss"
    RIGHTFUL_REJECTION = "rightful_rejection"
    REJECTED_WOULD_WIN = "rejected_would_win"
    NON_COMPLIANT = "non_compliant"
    DESTROYED_BY_FEES = "destroyed_by_fees"
    DESTROYED_BY_SPREAD = "destroyed_by_spread"
    DESTROYED_BY_VOLATILITY = "destroyed_by_volatility"


class StrategyStatus(str, Enum):
    PRODUCTION = "production"
    CANDIDATE = "candidate"
    EXPERIMENTAL = "experimental"
    ARCHIVED = "archived"


# ============================================================
# Table : market_snapshots
# Stocke un instantané de marché toutes les X minutes.
# ============================================================


class MarketSnapshot(SQLModel, table=True):
    __tablename__ = "market_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    asset: str = Field(index=True)          # "BTC", "ETH"
    price: float
    volume_24h: Optional[float] = None      # volume sur 24h en USD
    price_change_1h: Optional[float] = None # variation % sur 1h
    price_change_24h: Optional[float] = None
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    volatility_estimate: Optional[float] = None  # écart-type ou ATR estimé
    spread_estimate: Optional[float] = None      # spread estimé en %
    source: str = Field(default="coingecko")     # fournisseur de données


# ============================================================
# Table : signals
# Signaux générés par le Market Scanner Agent.
# ============================================================


class Signal(SQLModel, table=True):
    __tablename__ = "signals"

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    asset: str = Field(index=True)
    timeframe: str                          # "1h", "4h", "1d"
    signal_type: str                        # "rsi_oversold", "ma_cross", "volume_spike"…
    signal_value: Optional[float] = None    # valeur numérique du signal (ex: RSI = 28.5)
    confidence: float = Field(ge=0.0, le=1.0)  # 0.0 à 1.0
    direction: Optional[str] = None         # "LONG", "SHORT", None si neutre
    explanation: Optional[str] = None       # texte lisible pour les logs


# ============================================================
# Table : paper_trades
# Historique complet de chaque trade simulé.
# ============================================================


class PaperTrade(SQLModel, table=True):
    __tablename__ = "paper_trades"

    id: Optional[int] = Field(default=None, primary_key=True)
    strategy_version: str = Field(index=True)   # "strategy_v1_production"
    asset: str = Field(index=True)
    direction: TradeDirection
    timeframe: Optional[str] = None

    # Cycle de vie
    entry_time: datetime = Field(default_factory=datetime.utcnow)
    exit_time: Optional[datetime] = None
    status: TradeStatus = Field(default=TradeStatus.OPEN)

    # Prix
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: float
    take_profit: float

    # Taille de position
    position_size_eur: float               # montant engagé en €
    position_size_asset: Optional[float] = None  # quantité d'actif

    # Frictions simulées
    fees_estimated: float = Field(default=0.0)
    spread_estimated: float = Field(default=0.0)
    slippage_estimated: float = Field(default=0.0)

    # P&L
    gross_pnl: Optional[float] = None      # avant frais
    net_pnl: Optional[float] = None        # après frais + spread + slippage
    return_percent: Optional[float] = None

    # Résultat
    result: TradeResult = Field(default=TradeResult.OPEN)
    category: Optional[TradeCategory] = None

    # Signaux et justification
    signals_favorable: Optional[str] = None    # JSON sérialisé
    signals_unfavorable: Optional[str] = None  # JSON sérialisé
    confidence_score: Optional[float] = None
    reason_entry: Optional[str] = None
    reason_exit: Optional[str] = None

    # Apprentissage
    main_error: Optional[str] = None
    lesson_learned: Optional[str] = None
    improvement_proposed: Optional[str] = None


# ============================================================
# Table : risk_decisions
# Chaque décision du Risk Manager est enregistrée.
# Les refus sont aussi des données d'apprentissage.
# ============================================================


class RiskDecisionLog(SQLModel, table=True):
    __tablename__ = "risk_decisions"

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    trade_id: Optional[int] = Field(default=None, foreign_key="paper_trades.id")

    asset: str
    direction: Optional[str] = None
    proposed_entry: Optional[float] = None
    proposed_stop_loss: Optional[float] = None
    proposed_take_profit: Optional[float] = None

    decision: RiskDecision
    reason: str                             # explication lisible
    rule_triggered: Optional[str] = None   # nom de la règle qui a bloqué
    risk_score: Optional[float] = None     # score de risque calculé (0.0–1.0)

    # Vérification a posteriori : le trade refusé aurait-il gagné ?
    would_have_won: Optional[bool] = None
    theoretical_pnl: Optional[float] = None


# ============================================================
# Table : learning_notes
# Notes générées par le Learning Agent après chaque trade.
# ============================================================


class LearningNote(SQLModel, table=True):
    __tablename__ = "learning_notes"

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    trade_id: Optional[int] = Field(default=None, foreign_key="paper_trades.id")

    category: TradeCategory
    lesson: str                             # leçon apprise
    improvement_proposal: Optional[str] = None  # amélioration proposée
    signal_quality: Optional[str] = None   # "good", "bad", "neutral"
    accepted_or_rejected: Optional[str] = None  # statut de la proposition

    # Contexte de marché au moment du trade
    market_context: Optional[str] = None   # JSON sérialisé


# ============================================================
# Table : strategy_versions
# Registre de toutes les versions de stratégies.
# Chaque version a ses propres métriques.
# ============================================================


class StrategyVersion(SQLModel, table=True):
    __tablename__ = "strategy_versions"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)   # "strategy_v1_production"
    version: str                                  # "1.0.0"
    status: StrategyStatus = Field(default=StrategyStatus.CANDIDATE)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    description: Optional[str] = None

    # Métriques synthétiques (mises à jour par le Learning Agent)
    total_trades: int = Field(default=0)
    win_rate: Optional[float] = None
    profit_factor: Optional[float] = None
    max_drawdown_percent: Optional[float] = None
    net_return_percent: Optional[float] = None
    avg_risk_reward: Optional[float] = None

    # Paramètres de la stratégie (JSON sérialisé)
    parameters: Optional[str] = None

    # Résumé du dernier rapport
    metrics_summary: Optional[str] = None
    last_evaluated_at: Optional[datetime] = None
