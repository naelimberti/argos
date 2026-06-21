"""
Tests de la couche base de données ARGOS.
Vérifie que les 6 tables fonctionnent correctement (CRUD).
"""

from datetime import datetime

from argos.database.models import (
    LearningNote,
    MarketSnapshot,
    PaperTrade,
    RiskDecisionLog,
    Signal,
    StrategyVersion,
    TradeCategory,
    TradeDirection,
    TradeResult,
    TradeStatus,
    RiskDecision,
    StrategyStatus,
)
from argos.database.repositories import (
    count_trades_today,
    get_consecutive_losses,
    get_daily_pnl,
    get_last_market_snapshot,
    get_open_positions,
    get_recent_signals,
    get_recent_trades,
    save_learning_note,
    save_market_snapshot,
    save_paper_trade,
    save_risk_decision,
    save_signal,
    update_paper_trade,
)


# ============================================================
# MarketSnapshot
# ============================================================


def test_save_and_retrieve_market_snapshot(session):
    snap = MarketSnapshot(asset="BTC", price=65000.0, source="coingecko")
    save_market_snapshot(session, snap)
    session.commit()

    result = get_last_market_snapshot(session, "BTC")
    assert result is not None
    assert result.price == 65000.0
    assert result.asset == "BTC"


def test_get_last_snapshot_returns_most_recent(session):
    snap1 = MarketSnapshot(
        asset="BTC", price=60000.0,
        timestamp=datetime(2026, 1, 1, 10, 0), source="coingecko"
    )
    snap2 = MarketSnapshot(
        asset="BTC", price=65000.0,
        timestamp=datetime(2026, 1, 1, 11, 0), source="coingecko"
    )
    save_market_snapshot(session, snap1)
    save_market_snapshot(session, snap2)
    session.commit()

    result = get_last_market_snapshot(session, "BTC")
    assert result.price == 65000.0


def test_snapshots_are_isolated_by_asset(session):
    save_market_snapshot(session, MarketSnapshot(asset="BTC", price=65000.0, source="coingecko"))
    session.commit()

    assert get_last_market_snapshot(session, "BTC").price == 65000.0
    assert get_last_market_snapshot(session, "ETH") is None


# ============================================================
# Signal
# ============================================================


def test_save_and_retrieve_signal(session):
    sig = Signal(
        asset="BTC", timeframe="1h",
        signal_type="rsi_oversold", signal_value=28.5,
        confidence=0.80, direction="LONG",
        explanation="RSI sous 30 — zone de survente",
    )
    save_signal(session, sig)
    session.commit()

    results = get_recent_signals(session, "BTC", hours=1)
    assert len(results) == 1
    assert results[0].signal_type == "rsi_oversold"
    assert results[0].confidence == 0.80


def test_signal_confidence_bounds_accepted(session):
    sig_min = Signal(asset="BTC", timeframe="4h", signal_type="test", confidence=0.0)
    sig_max = Signal(asset="BTC", timeframe="4h", signal_type="test", confidence=1.0)
    save_signal(session, sig_min)
    save_signal(session, sig_max)
    session.commit()

    results = get_recent_signals(session, "BTC", hours=1)
    assert len(results) == 2


# ============================================================
# PaperTrade
# ============================================================


def test_open_and_close_paper_trade(session):
    trade = PaperTrade(
        strategy_version="strategy_v1_production",
        asset="BTC",
        direction=TradeDirection.LONG,
        entry_price=65000.0,
        stop_loss=63000.0,
        take_profit=69000.0,
        position_size_eur=2.0,
        fees_estimated=0.002,
        spread_estimated=0.001,
        slippage_estimated=0.0005,
    )
    save_paper_trade(session, trade)
    session.commit()

    positions = get_open_positions(session)
    assert len(positions) == 1

    # Clôture du trade
    trade.exit_price = 69000.0
    trade.gross_pnl = (69000.0 - 65000.0) / 65000.0 * 2.0
    trade.net_pnl = trade.gross_pnl - trade.fees_estimated - trade.spread_estimated
    trade.result = TradeResult.WIN
    trade.status = TradeStatus.CLOSED
    trade.exit_time = datetime.utcnow()
    update_paper_trade(session, trade)
    session.commit()

    positions_after = get_open_positions(session)
    assert len(positions_after) == 0

    recent = get_recent_trades(session, limit=5)
    assert len(recent) == 1
    assert recent[0].result == TradeResult.WIN


def test_count_trades_today(session):
    for _ in range(3):
        t = PaperTrade(
            strategy_version="strategy_v1_production", asset="BTC",
            direction=TradeDirection.LONG, entry_price=65000.0,
            stop_loss=63000.0, take_profit=69000.0, position_size_eur=1.0,
        )
        save_paper_trade(session, t)
    session.commit()

    assert count_trades_today(session) == 3


def test_consecutive_losses(session):
    # 3 cycles distincts (minutes différentes) : WIN, LOSS, LOSS → 2 cycles perdants consécutifs
    from datetime import timedelta
    base = datetime.utcnow().replace(second=0, microsecond=0)
    for i, result in enumerate([TradeResult.WIN, TradeResult.LOSS, TradeResult.LOSS]):
        t = PaperTrade(
            strategy_version="strategy_v1_production", asset="BTC",
            direction=TradeDirection.LONG, entry_price=65000.0,
            stop_loss=63000.0, take_profit=69000.0, position_size_eur=1.0,
            status=TradeStatus.CLOSED, result=result,
            exit_time=base + timedelta(minutes=i),
            net_pnl=-0.05 if result == TradeResult.LOSS else 0.10,
        )
        save_paper_trade(session, t)
    session.commit()

    assert get_consecutive_losses(session) == 2


def test_daily_pnl_sum(session):
    for net_pnl in [0.10, -0.05, 0.08]:
        t = PaperTrade(
            strategy_version="strategy_v1_production", asset="BTC",
            direction=TradeDirection.LONG, entry_price=65000.0,
            stop_loss=63000.0, take_profit=69000.0, position_size_eur=1.0,
            status=TradeStatus.CLOSED,
            result=TradeResult.WIN if net_pnl > 0 else TradeResult.LOSS,
            exit_time=datetime.utcnow(), net_pnl=net_pnl,
        )
        save_paper_trade(session, t)
    session.commit()

    pnl = get_daily_pnl(session)
    assert abs(pnl - 0.13) < 0.001


# ============================================================
# RiskDecisionLog
# ============================================================


def test_save_risk_decision_rejected(session):
    decision = RiskDecisionLog(
        asset="BTC", direction="LONG",
        proposed_entry=65000.0, proposed_stop_loss=63000.0, proposed_take_profit=69000.0,
        decision=RiskDecision.REJECTED,
        reason="Spread trop élevé (0.45% > 0.30%)",
        rule_triggered="max_spread_percent",
        risk_score=0.75,
    )
    save_risk_decision(session, decision)
    session.commit()

    from sqlmodel import select
    results = list(session.exec(select(RiskDecisionLog)).all())
    assert len(results) == 1
    assert results[0].decision == RiskDecision.REJECTED
    assert results[0].rule_triggered == "max_spread_percent"


# ============================================================
# LearningNote
# ============================================================


def test_save_learning_note(session):
    note = LearningNote(
        trade_id=None,
        category=TradeCategory.DESTROYED_BY_FEES,
        lesson="Le spread estimé était sous-évalué. Gain brut positif mais net négatif.",
        improvement_proposal="Augmenter le seuil min_net_gain_after_fees à 0.30%.",
        signal_quality="bad",
        accepted_or_rejected="pending",
    )
    save_learning_note(session, note)
    session.commit()

    from sqlmodel import select
    results = list(session.exec(select(LearningNote)).all())
    assert len(results) == 1
    assert results[0].category == TradeCategory.DESTROYED_BY_FEES


# ============================================================
# StrategyVersion
# ============================================================


def test_strategy_version_created(session):
    strategy = StrategyVersion(
        name="strategy_v1_production",
        version="1.0.0",
        status=StrategyStatus.PRODUCTION,
        description="Stratégie initiale RSI + MA",
    )
    session.add(strategy)
    session.commit()

    from sqlmodel import select
    result = session.exec(
        select(StrategyVersion).where(StrategyVersion.name == "strategy_v1_production")
    ).first()

    assert result is not None
    assert result.status == StrategyStatus.PRODUCTION
    assert result.total_trades == 0
