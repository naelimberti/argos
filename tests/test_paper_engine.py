"""
Tests du Paper Trading Engine ARGOS.

PrioritÃ© absolue : vÃ©rifier que les rÃ¨gles de sÃ©curitÃ© bloquent correctement.
Un moteur financier qui laisse passer un mauvais trade est plus dangereux
qu'un moteur qui en refuse trop.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from argos.database.models import TradeDirection, TradeResult, TradeStatus
from argos.trading.paper_engine import PaperTradingEngine, TradeRequest
from argos.trading.portfolio import Portfolio
from argos.trading.trade_lifecycle import TradeMetrics, compute_trade_metrics, describe_trade

os.environ["TRADING_MODE"] = "paper"
os.environ["ENABLE_REAL_TRADING"] = "false"


# ============================================================
# Fixtures
# ============================================================

RISK_RULES = {
    "paper_trading": {
        "initial_capital_eur": 10.0,
        "max_trades_per_day": 5,
        "max_daily_loss_percent": 5.0,
        "max_open_positions": 1,
        "max_position_size_percent": 20.0,
        "max_consecutive_losses": 2,
        "cooldown_after_loss_minutes": 60,
        "stop_loss_required": True,
        "take_profit_required": True,
        "allow_leverage": False,
        "min_risk_reward_ratio": 2.0,
        "min_confidence_score": 0.70,
    },
    "fees": {
        "taker_fee_percent": 0.10,
        "slippage_estimate_percent": 0.05,
        "default_spread_percent": 0.10,
    },
}


@pytest.fixture
def portfolio():
    return Portfolio(initial_capital=10.0)


@pytest.fixture
def paper_engine(portfolio):
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        return PaperTradingEngine(portfolio)


def _valid_long_request(**overrides) -> TradeRequest:
    """Trade LONG valide par dÃ©faut â€” BTC Ã  55 000 EUR."""
    defaults = dict(
        asset="BTC",
        direction=TradeDirection.LONG,
        entry_price=55_000.0,
        stop_loss=53_000.0,       # -3.6% â†’ risque = 2000 EUR / 55000 * taille
        take_profit=59_000.0,     # +7.3% â†’ R/R = 4000/2000 = 2.0 exactement
        position_size_eur=2.0,    # 20% de 10 EUR
        strategy_version="strategy_v1_production",
        confidence_score=0.80,
        reason_entry="Test LONG valide",
    )
    defaults.update(overrides)
    return TradeRequest(**defaults)


def _valid_short_request(**overrides) -> TradeRequest:
    """Trade SHORT valide par dÃ©faut â€” ETH Ã  2 800 EUR."""
    defaults = dict(
        asset="BTC",
        direction=TradeDirection.SHORT,
        entry_price=55_000.0,
        stop_loss=57_000.0,        # +3.6% au-dessus
        take_profit=49_500.0,      # -10% en-dessous â†’ R/R = 280/100 = 2.8
        position_size_eur=1.0,
        strategy_version="strategy_v1_production",
        confidence_score=0.75,
    )
    defaults.update(overrides)
    return TradeRequest(**defaults)


# ============================================================
# SÃ©curitÃ© : garde mode rÃ©el
# ============================================================

def test_engine_refuses_to_start_in_real_mode(portfolio):
    with patch.dict(os.environ, {"TRADING_MODE": "real"}):
        with pytest.raises(RuntimeError, match="SECURITE"):
            PaperTradingEngine(portfolio)


def test_engine_refuses_if_real_trading_enabled(portfolio):
    with patch.dict(os.environ, {"ENABLE_REAL_TRADING": "true"}):
        with pytest.raises(RuntimeError, match="SECURITE"):
            PaperTradingEngine(portfolio)


# ============================================================
# RÃ¨gle : stop-loss et take-profit obligatoires
# ============================================================

def test_trade_refused_without_stop_loss(paper_engine, session):
    req = _valid_long_request(stop_loss=0)
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(req, session)
    assert not result.accepted
    assert "stop-loss" in result.rejection_reason.lower()


def test_trade_refused_without_take_profit(paper_engine, session):
    req = _valid_long_request(take_profit=0)
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(req, session)
    assert not result.accepted
    assert "take-profit" in result.rejection_reason.lower()


def test_long_refused_if_sl_above_entry(paper_engine, session):
    req = _valid_long_request(stop_loss=57_000.0)  # SL au-dessus du prix d'entrÃ©e
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(req, session)
    assert not result.accepted
    assert "stop-loss" in result.rejection_reason.lower()


def test_long_refused_if_tp_below_entry(paper_engine, session):
    req = _valid_long_request(take_profit=53_000.0)  # TP sous le prix d'entrÃ©e
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(req, session)
    assert not result.accepted


def test_short_refused_if_sl_below_entry(paper_engine, session):
    req = _valid_short_request(stop_loss=2_700.0)  # SL sous le prix d'entrÃ©e pour un SHORT
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(req, session)
    assert not result.accepted


# ============================================================
# RÃ¨gle : pas de levier
# ============================================================

def test_trade_refused_if_size_exceeds_capital(paper_engine, session):
    req = _valid_long_request(position_size_eur=15.0)  # > 10 EUR de capital
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(req, session)
    assert not result.accepted
    assert "levier" in result.rejection_reason.lower() or "capital" in result.rejection_reason.lower()


# ============================================================
# RÃ¨gle : 1 position max
# ============================================================

def test_second_trade_refused_if_position_open(paper_engine, session):
    req = _valid_long_request()
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        first = paper_engine.open_trade(req, session)
        assert first.accepted

        second = paper_engine.open_trade(_valid_short_request(), session)
        assert not second.accepted
        assert "position" in second.rejection_reason.lower()


# ============================================================
# RÃ¨gle : ratio risque/rendement minimum
# ============================================================

def test_trade_refused_if_rr_too_low(paper_engine, session):
    # R/R = (55500-55000) / (55000-53000) = 500/2000 = 0.25 < 2.0
    req = _valid_long_request(take_profit=55_500.0)
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(req, session)
    assert not result.accepted
    assert "risque/rendement" in result.rejection_reason.lower()


def test_trade_accepted_with_exact_min_rr(paper_engine, session):
    # R/R = (59000-55000) / (55000-53000) = 4000/2000 = 2.0 exactement
    req = _valid_long_request()
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(req, session)
    assert result.accepted


# ============================================================
# RÃ¨gle : taille de position max (20% du capital)
# ============================================================

def test_trade_refused_if_size_too_large(paper_engine, session):
    # 20% de 10 EUR = 2 EUR max â†’ 3 EUR dÃ©passe
    req = _valid_long_request(position_size_eur=3.0)
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(req, session)
    assert not result.accepted
    assert "taille" in result.rejection_reason.lower()


# ============================================================
# RÃ¨gle : perte journaliÃ¨re max 5%
# ============================================================

def test_trade_refused_if_daily_loss_limit_reached(paper_engine, session):
    # Simuler une perte de 0.50 EUR = 5% de 10 EUR
    with patch("argos.trading.paper_engine.get_daily_pnl", return_value=-0.50), \
         patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(_valid_long_request(), session)
    assert not result.accepted
    assert "perte journaliere" in result.rejection_reason.lower()


def test_trade_allowed_if_daily_loss_just_below_limit(paper_engine, session):
    # -0.49 EUR < -0.50 EUR (5% de 10 EUR) â†’ autorisÃ©
    with patch("argos.trading.paper_engine.get_daily_pnl", return_value=-0.49), \
         patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(_valid_long_request(), session)
    assert result.accepted


# ============================================================
# RÃ¨gle : arrÃªt aprÃ¨s pertes consÃ©cutives
# ============================================================

def test_trade_refused_after_consecutive_losses(paper_engine, session):
    with patch("argos.trading.paper_engine.get_consecutive_losses", return_value=2), \
         patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(_valid_long_request(), session)
    assert not result.accepted
    assert "consecutives" in result.rejection_reason.lower()


def test_trade_allowed_with_one_loss(paper_engine, session):
    with patch("argos.trading.paper_engine.get_consecutive_losses", return_value=1), \
         patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(_valid_long_request(), session)
    assert result.accepted


# ============================================================
# Ouverture d'un trade valide
# ============================================================

def test_valid_trade_opens_correctly(paper_engine, session):
    req = _valid_long_request()
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        result = paper_engine.open_trade(req, session)

    assert result.accepted
    assert result.trade_id is not None

    # VÃ©rifier en base
    from argos.database.repositories import get_open_positions
    positions = get_open_positions(session)
    assert len(positions) == 1
    trade = positions[0]
    assert trade.asset == "BTC"
    assert trade.direction == TradeDirection.LONG
    assert trade.entry_price == 55_000.0
    assert trade.status == TradeStatus.OPEN
    assert trade.fees_estimated > 0
    assert trade.spread_estimated > 0


def test_valid_trade_registers_risk_decision(paper_engine, session):
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        paper_engine.open_trade(_valid_long_request(), session)

    from sqlmodel import select
    from argos.database.models import RiskDecisionLog, RiskDecision
    decisions = list(session.exec(select(RiskDecisionLog)).all())
    assert len(decisions) == 1
    assert decisions[0].decision == RiskDecision.APPROVED


def test_refused_trade_registers_rejection(paper_engine, session):
    req = _valid_long_request(stop_loss=0)
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        paper_engine.open_trade(req, session)

    from sqlmodel import select
    from argos.database.models import RiskDecisionLog, RiskDecision
    decisions = list(session.exec(select(RiskDecisionLog)).all())
    assert len(decisions) == 1
    assert decisions[0].decision == RiskDecision.REJECTED
    assert decisions[0].rule_triggered == "stop_loss_required"


# ============================================================
# Surveillance et clÃ´ture des positions
# ============================================================

def test_long_closed_on_stop_loss(paper_engine, session):
    req = _valid_long_request()
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        paper_engine.open_trade(req, session)

    # Prix descend sous le SL
    closed = paper_engine.monitor_open_positions({"BTC": 52_000.0}, session)
    assert len(closed) == 1
    assert closed[0].result == TradeResult.LOSS
    assert closed[0].status == TradeStatus.CLOSED
    assert closed[0].net_pnl < 0


def test_long_closed_on_take_profit(paper_engine, session):
    req = _valid_long_request()
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        paper_engine.open_trade(req, session)

    # Prix monte au-dessus du TP
    closed = paper_engine.monitor_open_positions({"BTC": 60_000.0}, session)
    assert len(closed) == 1
    assert closed[0].result == TradeResult.WIN
    assert closed[0].net_pnl > 0


def test_short_closed_on_stop_loss(paper_engine, session):
    req = _valid_short_request()
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        paper_engine.open_trade(req, session)

    # Prix monte au-dessus du SL
    closed = paper_engine.monitor_open_positions({"BTC": 58_000.0}, session)
    assert len(closed) == 1
    assert closed[0].result == TradeResult.LOSS


def test_short_closed_on_take_profit(paper_engine, session):
    req = _valid_short_request()
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        paper_engine.open_trade(req, session)

    # Prix descend sous le TP
    closed = paper_engine.monitor_open_positions({"BTC": 48_000.0}, session)
    assert len(closed) == 1
    assert closed[0].result == TradeResult.WIN


def test_position_not_closed_if_price_between_sl_and_tp(paper_engine, session):
    req = _valid_long_request()
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        paper_engine.open_trade(req, session)

    # Prix entre SL (53000) et TP (59000) â†’ position reste ouverte
    closed = paper_engine.monitor_open_positions({"BTC": 56_000.0}, session)
    assert len(closed) == 0


def test_position_closed_on_timeout(paper_engine, session):
    req = _valid_long_request()
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        paper_engine.open_trade(req, session)

    # Simuler un trade vieux de 49h
    from argos.database.repositories import get_open_positions
    trade = get_open_positions(session)[0]
    trade.entry_time = datetime.now(timezone.utc) - timedelta(hours=49)
    session.add(trade)
    session.commit()

    closed = paper_engine.monitor_open_positions({"BTC": 56_000.0}, session, max_trade_duration_hours=48)
    assert len(closed) == 1
    assert closed[0].result == TradeResult.TIMEOUT


# ============================================================
# P&L â€” vÃ©rification du calcul
# ============================================================

def test_long_win_pnl_calculation(paper_engine, session):
    req = _valid_long_request(
        entry_price=55_000.0,
        take_profit=59_000.0,
        position_size_eur=2.0,
    )
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        paper_engine.open_trade(req, session)

    paper_engine.monitor_open_positions({"BTC": 60_000.0}, session)  # TP touchÃ©

    from argos.database.repositories import get_recent_trades
    trade = get_recent_trades(session, limit=1)[0]

    # Gain brut = (59000-55000)/55000 * 2.0 = 4000/55000 * 2 â‰ˆ 0.1454 EUR
    expected_gross = (59_000 - 55_000) / 55_000 * 2.0
    assert abs(trade.gross_pnl - expected_gross) < 0.001
    # Net < brut (frais dÃ©duits)
    assert trade.net_pnl < trade.gross_pnl


def test_portfolio_updated_after_close(paper_engine, session):
    initial_capital = paper_engine.portfolio.current_capital

    req = _valid_long_request()
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        paper_engine.open_trade(req, session)

    paper_engine.monitor_open_positions({"BTC": 60_000.0}, session)  # WIN

    assert paper_engine.portfolio.current_capital != initial_capital
    assert paper_engine.portfolio.total_trades == 1
    assert paper_engine.portfolio.winning_trades == 1


def test_portfolio_tracks_loss(paper_engine, session):
    req = _valid_long_request()
    with patch("argos.trading.paper_engine._load_risk_rules", return_value=RISK_RULES):
        paper_engine.open_trade(req, session)

    paper_engine.monitor_open_positions({"BTC": 52_000.0}, session)  # SL touchÃ©

    assert paper_engine.portfolio.losing_trades == 1
    assert paper_engine.portfolio.winning_trades == 0
    assert paper_engine.portfolio.current_capital < paper_engine.portfolio.initial_capital


# ============================================================
# TradeLifecycle â€” mÃ©triques
# ============================================================

def test_compute_metrics_long():
    m = compute_trade_metrics(
        direction=TradeDirection.LONG,
        entry_price=55_000.0,
        stop_loss=53_000.0,
        take_profit=59_000.0,
        position_size_eur=2.0,
        capital_eur=10.0,
    )
    assert m.risk_reward_ratio == pytest.approx(2.0, rel=0.01)
    assert m.risk_eur > 0
    assert m.reward_eur > 0
    assert m.risk_pct > 0


def test_compute_metrics_short():
    m = compute_trade_metrics(
        direction=TradeDirection.SHORT,
        entry_price=55_000.0,
        stop_loss=57_000.0,
        take_profit=49_500.0,
        position_size_eur=1.0,
        capital_eur=10.0,
    )
    assert m.risk_reward_ratio > 2.0
    assert m.risk_eur > 0


def test_describe_trade_returns_string():
    desc = describe_trade("BTC", TradeDirection.LONG, 55_000, 53_000, 59_000, 2.0, 10.0)
    assert "BTC" in desc
    assert "LONG" in desc
    assert "R/R" in desc


def test_portfolio_summary():
    p = Portfolio(initial_capital=10.0)
    p.apply_trade_result(net_pnl=0.15, fees=0.004, won=True)
    s = p.summary()
    assert s["current_capital"] == pytest.approx(10.15)
    assert s["win_rate"] == 100.0
    assert s["total_trades"] == 1




