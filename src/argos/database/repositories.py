"""
Repositories ARGOS — accès en lecture/écriture à la base de données.

Chaque fonction prend une Session en paramètre.
Aucune logique métier ici : uniquement du CRUD propre.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from argos.database.models import (
    LearningNote,
    MarketSnapshot,
    PaperTrade,
    RiskDecisionLog,
    Signal,
    StrategyVersion,
    TradeStatus,
)
from argos.utils.logger import get_logger

logger = get_logger("repositories")


# ============================================================
# MarketSnapshot
# ============================================================


def save_market_snapshot(session: Session, snapshot: MarketSnapshot) -> MarketSnapshot:
    session.add(snapshot)
    session.flush()
    logger.debug(f"Snapshot enregistré : {snapshot.asset} @ {snapshot.price}")
    return snapshot


def get_last_market_snapshot(session: Session, asset: str) -> Optional[MarketSnapshot]:
    return session.exec(
        select(MarketSnapshot)
        .where(MarketSnapshot.asset == asset)
        .order_by(MarketSnapshot.timestamp.desc())  # type: ignore
        .limit(1)
    ).first()


def get_market_snapshots(
    session: Session,
    asset: str,
    since: Optional[datetime] = None,
    limit: int = 500,
) -> list[MarketSnapshot]:
    query = select(MarketSnapshot).where(MarketSnapshot.asset == asset)
    if since:
        query = query.where(MarketSnapshot.timestamp >= since)
    query = query.order_by(MarketSnapshot.timestamp.asc()).limit(limit)  # type: ignore
    return list(session.exec(query).all())


# ============================================================
# Signal
# ============================================================


def save_signal(session: Session, signal: Signal) -> Signal:
    session.add(signal)
    session.flush()
    logger.debug(f"Signal enregistré : {signal.asset} {signal.signal_type} conf={signal.confidence:.2f}")
    return signal


def get_recent_signals(
    session: Session,
    asset: str,
    hours: int = 1,
) -> list[Signal]:
    since = datetime.utcnow() - timedelta(hours=hours)
    return list(
        session.exec(
            select(Signal)
            .where(Signal.asset == asset, Signal.timestamp >= since)
            .order_by(Signal.timestamp.desc())  # type: ignore
        ).all()
    )


# ============================================================
# PaperTrade
# ============================================================


def save_paper_trade(session: Session, trade: PaperTrade) -> PaperTrade:
    session.add(trade)
    session.flush()
    logger.info(f"Trade simulé ouvert : {trade.asset} {trade.direction} @ {trade.entry_price}")
    return trade


def update_paper_trade(session: Session, trade: PaperTrade) -> PaperTrade:
    session.add(trade)
    session.flush()
    logger.info(
        f"Trade simulé mis à jour : id={trade.id} result={trade.result} "
        f"net_pnl={trade.net_pnl}"
    )
    return trade


def get_open_positions(session: Session) -> list[PaperTrade]:
    return list(
        session.exec(
            select(PaperTrade).where(PaperTrade.status == TradeStatus.OPEN)
        ).all()
    )


def get_recent_trades(session: Session, limit: int = 20) -> list[PaperTrade]:
    return list(
        session.exec(
            select(PaperTrade)
            .where(PaperTrade.status != TradeStatus.OPEN)
            .order_by(PaperTrade.exit_time.desc())  # type: ignore
            .limit(limit)
        ).all()
    )


def get_trades_since(session: Session, since: datetime) -> list[PaperTrade]:
    return list(
        session.exec(
            select(PaperTrade).where(PaperTrade.entry_time >= since)
        ).all()
    )


def count_trades_today(session: Session) -> int:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return len(
        session.exec(
            select(PaperTrade).where(PaperTrade.entry_time >= today)
        ).all()
    )


def get_consecutive_losses(session: Session) -> int:
    """
    Pertes consécutives comptées par cycle (pas par position).
    Plusieurs positions fermées dans la même minute = 1 seul événement.
    Un cycle est une perte si TOUTES ses positions sont des pertes.
    """
    from argos.database.models import TradeResult
    from collections import defaultdict

    trades = list(
        session.exec(
            select(PaperTrade)
            .where(PaperTrade.status == TradeStatus.CLOSED)
            .order_by(PaperTrade.exit_time.desc())  # type: ignore
            .limit(50)
        ).all()
    )

    # Grouper par minute de clôture
    by_minute: dict = defaultdict(list)
    for t in trades:
        if t.exit_time:
            key = t.exit_time.replace(second=0, microsecond=0)
            by_minute[key].append(t.result)

    # Trier du plus récent au plus ancien
    sorted_minutes = sorted(by_minute.keys(), reverse=True)

    count = 0
    for minute in sorted_minutes:
        results = by_minute[minute]
        all_loss = all(r == TradeResult.LOSS for r in results)
        if all_loss:
            count += 1
        else:
            break
    return count


def get_daily_pnl(session: Session) -> float:
    """Retourne le P&L net cumulé depuis minuit UTC."""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    trades = session.exec(
        select(PaperTrade).where(
            PaperTrade.exit_time >= today,
            PaperTrade.status == TradeStatus.CLOSED,
        )
    ).all()
    return sum(t.net_pnl or 0.0 for t in trades)


def get_portfolio_summary(session: Session) -> dict[str, Any]:
    """Résumé du portefeuille pour la commande 'status'."""
    from argos.database.models import TradeResult

    all_closed = list(
        session.exec(
            select(PaperTrade).where(PaperTrade.status == TradeStatus.CLOSED)
        ).all()
    )

    total_pnl = sum(t.net_pnl or 0.0 for t in all_closed)
    wins = [t for t in all_closed if t.result == TradeResult.WIN]
    win_rate = (len(wins) / len(all_closed) * 100) if all_closed else 0.0

    try:
        from argos.trading.paper_engine import _load_risk_rules
        initial = float(_load_risk_rules()["paper_trading"]["initial_capital_eur"])
    except Exception:
        initial = 10.0

    return {
        "capital_eur": initial + total_pnl,
        "total_pnl": total_pnl,
        "total_trades": len(all_closed),
        "win_rate": win_rate,
    }


# ============================================================
# RiskDecisionLog
# ============================================================


def save_risk_decision(session: Session, decision: RiskDecisionLog) -> RiskDecisionLog:
    session.add(decision)
    session.flush()
    logger.info(
        f"Décision Risk Manager : {decision.decision} — {decision.asset} "
        f"({decision.reason})"
    )
    return decision


def get_risk_decisions_since(
    session: Session, since: datetime
) -> list[RiskDecisionLog]:
    return list(
        session.exec(
            select(RiskDecisionLog).where(RiskDecisionLog.timestamp >= since)
        ).all()
    )


# ============================================================
# LearningNote
# ============================================================


def save_learning_note(session: Session, note: LearningNote) -> LearningNote:
    session.add(note)
    session.flush()
    logger.debug(f"Note d'apprentissage enregistrée : trade_id={note.trade_id} catégorie={note.category}")
    return note


def get_learning_notes_since(
    session: Session, since: datetime
) -> list[LearningNote]:
    return list(
        session.exec(
            select(LearningNote).where(LearningNote.timestamp >= since)
        ).all()
    )


# ============================================================
# StrategyVersion
# ============================================================


def get_production_strategy(session: Session) -> Optional[StrategyVersion]:
    from argos.database.models import StrategyStatus

    return session.exec(
        select(StrategyVersion).where(
            StrategyVersion.status == StrategyStatus.PRODUCTION
        )
    ).first()


def get_all_strategies(session: Session) -> list[StrategyVersion]:
    return list(session.exec(select(StrategyVersion)).all())


def update_strategy_metrics(
    session: Session,
    strategy_name: str,
    metrics: dict[str, Any],
) -> Optional[StrategyVersion]:
    strategy = session.exec(
        select(StrategyVersion).where(StrategyVersion.name == strategy_name)
    ).first()

    if not strategy:
        logger.warning(f"Stratégie introuvable : {strategy_name}")
        return None

    for key, value in metrics.items():
        if hasattr(strategy, key):
            setattr(strategy, key, value)

    strategy.last_evaluated_at = datetime.utcnow()
    session.add(strategy)
    session.flush()
    return strategy


# ============================================================
# Reconstruction du portefeuille
# ============================================================


def load_portfolio_from_db(session: Session):
    """
    Reconstruit un objet Portfolio depuis l'historique des trades clôturés.

    Appelée au démarrage de chaque cycle pour que le capital reflète
    tous les trades passés, même ceux d'une session précédente.
    """
    from argos.trading.portfolio import Portfolio
    from argos.database.models import TradeResult

    try:
        from argos.trading.paper_engine import _load_risk_rules
        cfg     = _load_risk_rules()
        initial = float(cfg["paper_trading"]["initial_capital_eur"])
    except Exception:
        initial = 10.0

    all_closed = list(
        session.exec(
            select(PaperTrade).where(PaperTrade.status == TradeStatus.CLOSED)
        ).all()
    )

    portfolio = Portfolio(initial_capital=initial)

    for trade in all_closed:
        net_pnl = trade.net_pnl or 0.0
        fees = trade.fees_estimated or 0.0
        won = trade.result == TradeResult.WIN
        # Applique directement sans logger (reconstruction silencieuse)
        portfolio.current_capital += net_pnl
        portfolio.realized_pnl += net_pnl
        portfolio.total_fees_paid += fees
        portfolio.total_trades += 1
        if won:
            portfolio.winning_trades += 1
        else:
            portfolio.losing_trades += 1

    logger.debug(
        f"Portefeuille reconstruit : capital={portfolio.current_capital:.4f} EUR "
        f"({portfolio.total_trades} trades clôturés)"
    )
    return portfolio


# ============================================================
# Export CSV
# ============================================================


def export_all_to_csv(dest: Path) -> list[Path]:
    """Exporte toutes les tables en CSV dans le dossier dest."""
    import pandas as pd
    from sqlmodel import Session
    from argos.database.db import get_engine

    dest.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    exported: list[Path] = []

    tables = {
        "market_snapshots": MarketSnapshot,
        "signals": Signal,
        "paper_trades": PaperTrade,
        "risk_decisions": RiskDecisionLog,
        "learning_notes": LearningNote,
        "strategy_versions": StrategyVersion,
    }

    with Session(engine) as session:
        for table_name, model_class in tables.items():
            rows = session.exec(select(model_class)).all()
            if not rows:
                continue

            records = [r.model_dump() for r in rows]
            df = pd.DataFrame(records)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M")
            path = dest / f"{table_name}_{ts}.csv"
            df.to_csv(path, index=False, encoding="utf-8")
            exported.append(path)
            logger.info(f"Export CSV : {path} ({len(records)} lignes)")

    return exported
